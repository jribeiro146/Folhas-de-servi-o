const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const editor = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/document-editor.js"), "utf8");
// Exercise the actual list controller with a simulated DOM and HTTP transport.
const controller = editor.slice(
    editor.indexOf("    const refreshFileList ="),
    editor.indexOf('    toggleSidebarBtn?.addEventListener("click"'),
);

function setup(responses) {
    const requests = [];
    const messages = [];
    const selection = [];
    const searchEvents = [];
    const fileList = { innerHTML: "old cached cards" };
    const refreshBtn = { disabled: false, addEventListener() {} };
    const form = { value: "Unsaved technician work" };
    const context = {
        fileList, refreshBtn, form,
        activeFileName: "current-draft",
        filesApp: { graphEnabled: false },
        fileSearch: { dispatchEvent: (event) => searchEvents.push(event.type) },
        Event: class { constructor(type) { this.type = type; } },
        setActiveCard: (name) => selection.push(name),
        showToast: (message, kind) => messages.push({ message, kind }),
        window: {
            setTimeout: (callback) => callback(),
            location: { reload() { assert.fail("Refreshing the list must preserve the open editor"); } },
        },
        fetch: async (url) => {
            requests.push(url);
            assert.ok(responses.length, `Unexpected request: ${url}`);
            const response = responses.shift();
            return { ok: response.ok !== false, json: async () => response };
        },
    };
    vm.runInNewContext(`${controller}\nthis.refresh = refreshFileList;`, context);
    return { context, requests, messages, selection, searchEvents };
}

test("waits for SharePoint, replaces old cards and preserves the open form and search", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: true }, html: "stale response" },
        { success: true, refresh: { in_progress: false, last_error: null } },
        { success: true, html: "only confirmed SharePoint cards" },
    ]);
    await run.context.refresh();
    assert.deepEqual(run.requests, ["/api/files?refresh=1", "/api/graph/status", "/api/files?refresh=0"]);
    assert.equal(run.context.fileList.innerHTML, "only confirmed SharePoint cards");
    assert.equal(run.context.form.value, "Unsaved technician work");
    assert.deepEqual(run.selection, ["current-draft"]);
    assert.deepEqual(run.searchEvents, ["input"]);
    assert.equal(run.context.refreshBtn.disabled, false);
});

test("a failed SharePoint refresh preserves the displayed list and reports no success", async () => {
    const run = setup([{ success: true, refresh: { in_progress: false, last_error: "Synthetic failure" } }]);
    await run.context.refresh();
    assert.equal(run.context.fileList.innerHTML, "old cached cards");
    assert.equal(run.messages.at(-1).kind, "error");
    assert.equal(run.context.refreshBtn.disabled, false);
    assert.equal(run.requests.length, 1);
});

test("background refresh requests the throttled endpoint and updates without notifications", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: false, last_error: null } },
        { success: true, html: "updated cards" },
    ]);
    await run.context.refresh({ silent: true });
    assert.equal(run.requests[0], "/api/files");
    assert.equal(run.context.fileList.innerHTML, "updated cards");
    assert.deepEqual(run.messages, []);
});

test("a refresh that is still running does not claim completion or replace cards", async () => {
    const run = setup(Array.from({ length: 41 }, () => ({ success: true, refresh: { in_progress: true } })));
    await run.context.refresh();
    assert.equal(run.context.fileList.innerHTML, "old cached cards");
    assert.equal(run.messages.at(-1).kind, "error");
    assert.equal(run.requests.length, 41);
});
