const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/admin.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

async function panel(job, { documents = [], section = "admin-jobs", failure = null } = {}) {
    const element = () => ({
        textContent: "", children: [], dataset: {}, disabled: false,
        classList: { add() {} }, addEventListener() {},
        append(child) { this.children.push(child); },
        replaceChildren() { this.children = []; },
    });
    const elements = new Map();
    const get = id => {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    };
    const document = {
        body: { dataset: { adminCsrf: "synthetic" } },
        getElementById: get, createElement: element,
    };
    const fetch = async () => ({
        ok: !failure, json: async () => failure || ({ success: true, documents, jobs: job ? [job] : [], audit: [],
            refresh: null, queue_summary: { pending: 0 } }),
    });
    vm.runInNewContext(source, { document, fetch, Date });
    await settle();
    const walk = node => [node.textContent, ...node.children.flatMap(walk)];
    return walk(get(section)).join("\n");
}

const job = { id: "synthetic", kind: "notify_teams", status: "failed", attempts: 3 };

for (const status of ["held", "pending", "running"]) {
    test("admin does not call an unsent " + status + " mail uncertain", async () => {
        const text = await panel({ ...job, status, kind: "archive_and_mail_local", attempts: 0,
            error_code: null, last_error: null, reconciliation_required: true, can_retry: false });
        assert.match(text, /A aguardar processamento/);
        assert.doesNotMatch(text, /Resultado incerto|Requer revisão|Repetir trabalho/);
    });
}

for (const [code, label] of [
    ["job_reconciliation_required", /Resultado incerto; é necessária reconciliação/],
    ["job_invalid_state", /Estado inválido; é necessária recuperação/],
    ["job_failed", /Falha de processamento/],
]) {
    test("admin displays the safe diagnostic category " + code, async () => {
        const text = await panel({ ...job, error_code: code, last_error: "Falha registada.", can_retry: false });
        assert.match(text, label);
        assert.match(text, /Operação: synthetic/);
        assert.doesNotMatch(text, /Repetir trabalho/);
    });
}

test("admin displays the request reference returned in a rejection message", async () => {
    const text = await panel(null, { section: "admin-status", failure: { success: false,
        error: "Pedido recusado. Referência: ref-sintetica.", reference: "ref-sintetica" } });
    assert.match(text, /ref-sintetica/);
});

test("admin explains a future throttle deadline without offering premature retry", async () => {
    const text = await panel({ ...job, retry_not_before: Date.now() / 1000 + 86400, can_retry: false });
    assert.match(text, /bloqueada pelo Teams até/);
    assert.doesNotMatch(text, /Repetir trabalho/);
});

test("admin explains deadlines outside the browser date range", async () => {
    const text = await panel({ ...job, retry_not_before: 1e300, can_retry: false });
    assert.match(text, /prazo muito distante/);
    assert.doesNotMatch(text, /Invalid Date|Repetir trabalho/);
});

test("admin allows an eligible retry after the deadline without a stale warning", async () => {
    const text = await panel({ ...job, retry_not_before: Date.now() / 1000 - 3600, can_retry: true });
    assert.match(text, /Repetir trabalho/);
    assert.doesNotMatch(text, /bloqueada pelo Teams|prazo muito distante/);
});

test("admin explains quarantine without offering release or restore controls", async () => {
    const text = await panel(null, { section: "admin-documents", documents: [
        { document_id: "synthetic", name: "synthetic", quarantined: true, code: "editing_state_quarantined" },
    ] });
    assert.match(text, /requer recuperação/i);
    assert.match(text, /preservad/);
    assert.doesNotMatch(text, /Expulsar|Repor acesso|Proprietário:|Sessões: undefined/);
});
