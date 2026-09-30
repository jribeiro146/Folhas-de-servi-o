const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const editor = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/document-editor.js"), "utf8");
const save = editor.slice(editor.indexOf("    const commitMessageKey ="), editor.indexOf("    const sendFile ="));

for (const status of ["pending", "local"]) {
    test("draft save reports " + status + " and opens the saved draft without another POST", async () => {
        const stored = new Map();
        const requests = [];
        const commits = [];
        const result = { success: true, file: "saved draft", created_copy: true,
            publication_status: status, graph_job_id: status === "pending" ? "draft:job" : null };
        const context = {
            activeFileName: "original", hideConfirm() {}, setBusy() {}, commitPhotoState() {},
            collectFormData: () => ({}), requireOperationMetadata: async () => ({}),
            buildCommitRequest: () => ({}), showToast: () => assert.fail("Unexpected save failure"),
            fetch: async (url, options) => { requests.push([url, options.method]); return {ok: true, json: async () => result}; },
            window: {
                location: { href: "" },
                sessionStorage: { setItem: (key, value) => stored.set(key, value) },
                __EDITING_COORDINATOR__: { markCommitted: async (value) => commits.push(value) },
            },
        };
        vm.runInNewContext(save + "\nthis.save = saveDraft;", context);
        await context.save();
        assert.deepEqual(requests, [["/api/file/original/draft", "POST"]]);
        assert.deepEqual(commits, [result]);
        assert.equal(context.window.location.href, "/?file=saved%20draft");
        const feedback = JSON.parse(stored.get("sensorpoint-commit-message"));
        assert.equal(feedback.variant, status === "pending" ? "info" : "success");
        if (status === "pending") assert.match(feedback.message, /SharePoint está pendente/);
    });
}

test("redirect preserves pending severity and accepts messages left by an older tab", () => {
    const feedback = editor.slice(editor.indexOf("\n    try {", editor.indexOf("window.__FILES_EDITOR_READY__")), editor.lastIndexOf("\n});"));
    for (const stored of ['{"message":"Publicação pendente","variant":"info"}', "Gravado anteriormente"]) {
        const seen = [];
        let removed = false;
        const context = { commitMessageKey: "message", showToast: (message, variant) => seen.push([message, variant]),
            window: { sessionStorage: { getItem: () => stored, removeItem: () => { removed = true; } } } };
        vm.runInNewContext(feedback, context);
        assert.equal(removed, true);
        assert.deepEqual(seen, stored.startsWith("{") ? [["Publicação pendente", "info"]] : [[stored, "success"]]);
    }
});
