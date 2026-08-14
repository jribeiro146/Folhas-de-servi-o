const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const coordinatorSource = fs.readFileSync(
    path.join(__dirname, "../../src/web/static/js/editing-coordinator.js"),
    "utf8",
);

const waitFor = async (predicate, message) => {
    for (let attempt = 0; attempt < 100; attempt += 1) {
        if (predicate()) return;
        await new Promise((resolve) => setTimeout(resolve, 5));
    }
    throw new Error(message);
};

const classList = () => ({
    add() {},
    remove() {},
    toggle() {},
});

const element = () => ({
    hidden: false,
    disabled: false,
    textContent: "",
    dataset: {},
    classList: classList(),
    addEventListener() {},
    querySelectorAll() { return []; },
});

test("reacquires editing metadata after the page is frozen and resumed", async () => {
    const windowListeners = new Map();
    const documentListeners = new Map();
    const elements = new Map();
    const getElement = (id) => {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    };
    const form = getElement("service-form");
    let formPayload = { customer_name: "Cliente Teste" };
    let bootstrapRequests = 0;
    const releasedPayloads = [];
    let generatedId = 0;

    const document = {
        readyState: "complete",
        hidden: false,
        body: { classList: classList() },
        getElementById: getElement,
        querySelector() { return null; },
        addEventListener(type, listener) {
            documentListeners.set(type, listener);
        },
    };
    const navigator = {
        onLine: true,
        sendBeacon(_url, payload) {
            releasedPayloads.push(payload);
            return true;
        },
    };
    const sessionStorage = {
        values: new Map(),
        getItem(key) { return this.values.get(key) || null; },
        setItem(key, value) { this.values.set(key, value); },
    };
    ["btn-save-draft", "btn-save-send", "btn-cancel-file"].forEach((id) => {
        getElement(id).disabled = true;
    });
    const window = {
        __FILES_APP__: {
            selectedFileName: "2026_7001_2026-08-13_JF",
            selectedFileIsDraft: false,
            recoveryMaxAgeDays: 30,
            editorUser: { id: "user-1", display_name: "Técnico Teste" },
        },
        __FILES_EDITOR__: {
            collectFormData: () => ({ ...formPayload }),
            populateForm: (_name, payload) => { formPayload = { ...(payload || {}) }; },
            showToast() {},
            renderAttachments() {},
            hasPendingPhotoChanges: () => false,
        },
        crypto: {
            randomUUID: () => `generated-${++generatedId}`,
        },
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        addEventListener(type, listener) {
            windowListeners.set(type, listener);
        },
        location: {},
    };
    window.window = window;

    const fetch = async (url) => {
        assert.match(url, /\/bootstrap\?client_id=/);
        bootstrapRequests += 1;
        return {
            ok: true,
            status: 200,
            json: async () => ({
                success: true,
                is_draft: false,
                document: { customer_name: "Cliente Teste" },
                source_document: { customer_name: "Cliente Teste" },
                signatures: {},
                photos: [],
                editing: {
                    document_id: "document-1",
                    revision: bootstrapRequests,
                    server_document: { customer_name: "Cliente Teste" },
                    lease: { token: `lease-${bootstrapRequests}` },
                },
            }),
        };
    };

    const context = vm.createContext({
        Blob,
        console,
        document,
        fetch,
        navigator,
        sessionStorage,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        window,
    });
    vm.runInContext(coordinatorSource, context, { filename: "editing-coordinator.js" });

    await waitFor(
        () => getElement("btn-save-draft").disabled === false,
        "initial editing session did not become ready",
    );
    assert.equal(
        window.__EDITING_COORDINATOR__.operationMetadata("send").lease_token,
        "lease-1",
    );

    documentListeners.get("freeze")();
    assert.equal(getElement("btn-save-draft").disabled, true);
    assert.equal(releasedPayloads.length, 1);
    assert.throws(
        () => window.__EDITING_COORDINATOR__.operationMetadata("send"),
        /sessão de edição ainda não está pronta/i,
    );

    documentListeners.get("resume")();
    await waitFor(
        () => bootstrapRequests === 2 && getElement("btn-save-draft").disabled === false,
        "editing session was not reacquired after resume",
    );
    const resumedMetadata = await window.__EDITING_COORDINATOR__.prepareOperation("send");
    assert.equal(resumedMetadata.document_id, "document-1");
    assert.equal(resumedMetadata.lease_token, "lease-2");
    assert.equal(resumedMetadata.base_revision, 2);
    assert.ok(resumedMetadata.idempotency_key);
});
