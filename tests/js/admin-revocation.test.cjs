const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/editing-coordinator.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
};
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
const bootstrap = (token = "old-session") => response({
    success: true, document: { customer_name: "Fictício" },
    editing: { document_id: "synthetic-document", revision: 1, lease: { token } },
});

async function coordinator(fetch, { sendBeacon = () => true } = {}) {
    const elements = new Map();
    const timers = new Map();
    const intervals = new Map();
    const windowEvents = new Map();
    const documentEvents = new Map();
    const classes = () => ({ add() {}, remove() {}, toggle() {} });
    const input = { disabled: false, dataset: {} };
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            hidden: false, disabled: false, textContent: "", dataset: {}, classList: classes(),
            addEventListener() {}, querySelectorAll: () => id === "service-form" ? [input] : [],
        });
        return elements.get(id);
    };
    const document = {
        readyState: "complete", hidden: false, body: { classList: classes() },
        getElementById: element, querySelector: () => null,
        addEventListener: (name, handler) => documentEvents.set(name, handler),
    };
    let payload = { customer_name: "Fictício" };
    let nextId = 0;
    const window = {
        __FILES_APP__: { selectedFileName: "synthetic", selectedFileIsDraft: false, editorUser: { id: "fake-user" } },
        __FILES_EDITOR__: {
            collectFormData: () => ({ ...payload }), populateForm: (_name, value) => { payload = value; },
            renderAttachments() {}, showToast() {}, hasPendingPhotoChanges: () => false,
        },
        crypto: { randomUUID: () => String(++nextId) },
        setTimeout: (fn, delay) => { const id = ++nextId; timers.set(id, { fn, delay }); return id; },
        clearTimeout: id => timers.delete(id),
        setInterval: (fn, delay) => { intervals.set(delay, fn); return delay; },
        clearInterval: delay => intervals.delete(delay),
        addEventListener: (name, handler) => windowEvents.set(name, handler), location: {},
    };
    const context = vm.createContext({
        window, document, fetch, navigator: { onLine: true, sendBeacon }, Blob,
        sessionStorage: { getItem: () => "synthetic-client", setItem() {} },
    });
    vm.runInContext(source, context);
    await settle();
    return {
        api: window.__EDITING_COORDINATOR__, element, input, documentEvents, windowEvents,
        edit: value => { payload = value; window.__EDITING_COORDINATOR__.markDirty(); },
        autosave: () => [...timers.values()].find(timer => timer.delay === 750).fn(),
        heartbeat: () => intervals.get(15000)(),
    };
}

test("autosave and pagehide send usable idempotency keys with the current session", async () => {
    const requests = [];
    const beacons = [];
    const view = await coordinator((url, options) => {
        if (options?.body) requests.push({ url, payload: JSON.parse(options.body) });
        return bootstrap();
    }, { sendBeacon: (url, body) => { beacons.push({ url, body }); return true; } });
    view.edit({ customer_name: "Autosave fictício" });
    await view.autosave();
    const autosave = requests.find(item => item.url.endsWith("/autosave"));
    assert.ok(autosave);
    view.edit({ customer_name: "Última alteração fictícia" });
    view.windowEvents.get("pagehide")();
    assert.equal(beacons.length, 1);
    assert.ok(beacons[0].url.endsWith("/editing/close"));
    const closing = JSON.parse(await beacons[0].body.text());
    for (const payload of [autosave.payload, closing]) {
        assert.equal(payload._edit.document_id, "synthetic-document");
        assert.equal(payload._edit.lease_token, "old-session");
        assert.equal(typeof payload._edit.idempotency_key, "string");
        assert.ok(payload._edit.idempotency_key.trim());
    }
    assert.notEqual(closing._edit.idempotency_key, autosave.payload._edit.idempotency_key);
    assert.equal(closing.document.customer_name, "Última alteração fictícia");
});

test("late autosave response cannot clear recovery or enable actions after administrator revocation", async () => {
    const pending = deferred();
    const view = await coordinator(url => url.includes("/autosave") ? pending.promise : bootstrap());
    view.edit({ customer_name: "Alterações locais fictícias" });
    const saving = view.autosave();
    view.api.handleConflict({ code: "lease_revoked" });
    pending.resolve(response({ success: true, editing: {
        document_id: "synthetic-document", revision: 2, lease: { token: "old-session" },
    } }));
    await saving;
    await settle();
    assert.equal(view.element("btn-save-draft").disabled, true);
    assert.equal(view.input.disabled, true);
    assert.equal(view.api.isDirty(), true, "local recovery must remain pending after revocation");
    assert.match(view.element("autosave-status").textContent, /administrador/);
    await assert.rejects(view.api.prepareOperation("send"));
});

test("late resume response cannot reopen the form after administrator revocation", async () => {
    const pending = deferred();
    let requests = 0;
    const view = await coordinator(() => ++requests === 1 ? bootstrap() : pending.promise);
    view.documentEvents.get("freeze")();
    view.documentEvents.get("resume")();
    view.api.handleConflict({ code: "lease_revoked" });
    pending.resolve(bootstrap("new-but-revoked-session"));
    await settle();
    assert.equal(view.input.disabled, true);
    assert.equal(view.element("btn-save-draft").disabled, true);
    assert.match(view.element("editing-lock-title").textContent, /administrador/);
    assert.match(view.element("autosave-status").textContent, /administrador/);
});

test("heartbeat from an old session cannot revoke a newly restored session", async () => {
    const pending = deferred();
    let bootstraps = 0;
    const view = await coordinator(url => url.includes("/heartbeat") ? pending.promise : bootstrap(`session-${++bootstraps}`));
    const heartbeat = view.heartbeat();
    view.documentEvents.get("freeze")();
    view.documentEvents.get("resume")();
    await settle();
    assert.equal(view.api.operationMetadata("save").lease_token, "session-2");
    pending.resolve(response({ success: false, code: "lease_revoked" }, 423));
    await heartbeat;
    assert.equal(view.element("btn-save-draft").disabled, false);
    assert.equal(view.api.operationMetadata("save").lease_token, "session-2");
});

test("a slow heartbeat does not start overlapping session checks", async () => {
    const pending = deferred();
    let checks = 0;
    const view = await coordinator(url => {
        if (!url.includes("/heartbeat")) return bootstrap();
        checks += 1;
        return pending.promise;
    });
    const first = view.heartbeat();
    const second = view.heartbeat();
    pending.resolve(response({ success: true }));
    await Promise.all([first, second]);
    assert.equal(checks, 1);
});
