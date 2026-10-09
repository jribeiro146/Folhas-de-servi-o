const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/admin.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

async function panel(job) {
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
        ok: true, json: async () => ({ success: true, documents: [], jobs: [job], audit: [],
            refresh: null, queue_summary: { pending: 0 } }),
    });
    vm.runInNewContext(source, { document, fetch, Date });
    await settle();
    const walk = node => [node.textContent, ...node.children.flatMap(walk)];
    return walk(get("admin-jobs")).join("\n");
}

const job = { id: "synthetic", kind: "notify_teams", status: "failed", attempts: 3 };

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
