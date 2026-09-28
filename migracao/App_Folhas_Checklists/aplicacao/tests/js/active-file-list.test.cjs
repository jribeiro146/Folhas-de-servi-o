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
    const status = { textContent: "", dataset: {}, hidden: true };
    const context = {
        fileList, refreshBtn, form, status,
        document: { getElementById: () => status },
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

test("a failed SharePoint refresh renders the last confirmed inventory and reports no success", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: false, last_error: "Synthetic failure" } },
        { success: true, html: "last confirmed cards" },
    ]);
    await run.context.refresh();
    assert.equal(run.context.fileList.innerHTML, "last confirmed cards");
    assert.equal(run.messages.at(-1).kind, "error");
    assert.equal(run.context.refreshBtn.disabled, false);
    assert.equal(run.requests.length, 2);
    assert.equal(run.context.status.dataset.state, "warning");
});

test("completion from another process must match the requested refresh", async () => {
    const run = setup([
        { success: true, requested_refresh_id: "wanted", refresh: { refresh_id: "wanted", in_progress: true } },
        { success: true, refresh: { in_progress: false, last_completed_refresh_id: "older" } },
        { success: true, refresh: { in_progress: false, last_completed_refresh_id: "wanted" } },
        { success: true, html: "confirmed cards" },
    ]);
    await run.context.refresh();
    assert.equal(run.requests.filter(url => url === "/api/graph/status").length, 2);
    assert.equal(run.context.fileList.innerHTML, "confirmed cards");
});

test("a completed later refresh in the same generation satisfies the requested run", async () => {
    const run = setup([
        { success: true, requested_refresh_id: "A", requested_refresh_sequence: 4, requested_refresh_generation: "one", refresh: { refresh_id: "A", in_progress: true } },
        { success: true, refresh: { refresh_id: "B", in_progress: false, last_completed_refresh_id: "B", generation_id: "one", last_completed_sequence: 5 } },
        { success: true, html: "newer confirmed inventory", refresh: { inventory: { available: true, stale: false } } },
    ]);
    await run.context.refresh();
    assert.equal(run.requests.length, 3);
    assert.equal(run.context.fileList.innerHTML, "newer confirmed inventory");
    assert.equal(run.messages.at(-1).kind, "success");
});

test("a counter from a different generation does not satisfy a requested refresh", async () => {
    const run = setup([
        { success: true, requested_refresh_id: "A", requested_refresh_sequence: 4, requested_refresh_generation: "one", refresh: { refresh_id: "A", in_progress: true } },
        ...Array.from({length: 40}, () => ({ success: true, refresh: {
            refresh_id: "B", in_progress: false, last_completed_refresh_id: "B", generation_id: "two", last_completed_sequence: 99,
        } })),
    ]);
    await run.context.refresh();
    assert.equal(run.context.fileList.innerHTML, "old cached cards");
    assert.equal(run.messages.at(-1).kind, "error");
});

test("an older completion in the same generation cannot confirm newer work", async () => {
    const run = setup([
        { success: true, requested_refresh_id: "C", requested_refresh_sequence: 6, requested_refresh_generation: "one", refresh: { refresh_id: "C", in_progress: true } },
        { success: true, refresh: { refresh_id: "B", in_progress: false, last_completed_refresh_id: "B", generation_id: "one", last_completed_sequence: 5 } },
        { success: true, refresh: { refresh_id: "C", in_progress: false, last_completed_refresh_id: "C", generation_id: "one", last_completed_sequence: 6 } },
        { success: true, html: "C inventory" },
    ]);
    await run.context.refresh();
    assert.equal(run.requests.length, 4);
    assert.equal(run.context.fileList.innerHTML, "C inventory");
});

test("background failure remains visible without a success toast", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: false, last_error: "Download failed" } },
        { success: true, html: "removed sheet no longer present", refresh: {
            last_error: "Download failed", inventory: { available: true, updated_at: "2026-09-28T10:00:00Z" },
        } },
    ]);
    await run.context.refresh({ silent: true });
    assert.equal(run.context.fileList.innerHTML, "removed sheet no longer present");
    assert.equal(run.context.status.hidden, false);
    assert.equal(run.context.status.dataset.state, "warning");
    assert.match(run.context.status.textContent, /Download failed/);
    assert.match(run.context.status.textContent, /Último inventário confirmado/);
    assert.deepEqual(run.messages, []);
});

test("missing inventory never produces a success message", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: false } },
        { success: true, html: "no confirmed files", refresh: { inventory: { available: false } } },
    ]);
    await run.context.refresh();
    assert.equal(run.context.status.dataset.state, "warning");
    assert.equal(run.messages.some(message => message.kind === "success"), false);
});

test("inventory timestamps in Unix seconds are shown in the correct year", async () => {
    const run = setup([
        { success: true, refresh: { in_progress: false } },
        { success: true, html: "cards", refresh: { inventory: { available: true, updated_at: 1790593200 } } },
    ]);
    await run.context.refresh();
    assert.match(run.context.status.textContent, /2026/);
    assert.doesNotMatch(run.context.status.textContent, /1970/);
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
