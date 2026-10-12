const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/document-editor.js"), "utf8");
const actions = source.slice(source.indexOf("    const commitMessageKey ="), source.indexOf("    fileSearch?.addEventListener"));

for (const name of ["saveDraft", "sendFile", "cancelFile", "openDocumentPreview"]) {
    test(name + " shows the diagnostic reference without repeating the POST", async () => {
        const messages = [], requests = [];
        const error = "Não foi possível concluir o pedido. Referência: ref-sintetica.";
        const context = {
            activeFileName: "synthetic", mailEnabled: true, hideConfirm() {}, setBusy() {}, t: key => key,
            collectFormData: () => ({}), requireOperationMetadata: async () => ({}), buildCommitRequest: () => ({}),
            commitPhotoState: () => assert.fail("Refused operation cannot commit"),
            showToast: message => messages.push(message),
            fetch: async (url, options) => {
                requests.push([url, options.method]);
                return { ok: false, status: 503, json: async () => ({ success: false,
                    error, reference: "ref-sintetica", code: "operation_failed" }) };
            },
            window: {
                location: { href: "original" },
                open: () => ({ document: { write() {} }, close() {} }),
                __EDITING_COORDINATOR__: { handleConflict: () => false,
                    markCommitted: () => assert.fail("Refused operation cannot commit") },
            },
        };
        vm.runInNewContext(actions + "\nthis.run = " + name + ";", context);
        await context.run({ customer_email: "synthetic@example.invalid" });
        assert.deepEqual(messages, [error]);
        assert.equal(requests.length, 1);
        assert.equal(requests[0][1], "POST");
        assert.equal(context.window.location.href, "original");
    });
}
