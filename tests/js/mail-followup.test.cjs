const test = require("node:test");
const assert = require("node:assert/strict");
const {create, KEY, TTL, LIMIT} = require("../../src/web/static/js/mail-followup.js");
const monitor = require("../../src/web/static/js/mail-job-monitor.js");
const SCOPE = "a".repeat(64), OTHER = "b".repeat(64);
const memory = () => {
    const values = new Map();
    return {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value),
        removeItem: key => values.delete(key)};
};
const reply = (data, status = 200, headers = {}) => ({ok: status === 200, status,
    headers: {get: key => headers[key] || null}, json: async () => data});
const accepted = id => reply({success: true, job: {id, status: "complete", result: {mail: {accepted: true}}}});
function harness(t, {storage = memory(), scope = SCOPE, contextScope = SCOPE,
    job = id => accepted(id), contextStatus = 200, jobScope = scope, now = Date.now, trackerMonitor = monitor} = {}) {
    const calls = [], views = [];
    const tracker = create({storage, scope, now, monitor: trackerMonitor,
        onChange: view => views.push(view),
        fetchFn: async (url, options) => {
            calls.push({url, options});
            assert.equal(options.method || "GET", "GET");
            assert.equal(options.body, undefined);
            assert.equal(options.cache, "no-store");
            if (url === "/api/graph/monitor-context")
                return reply({success: true, scope: contextScope},
                    typeof contextStatus === "function" ? contextStatus() : contextStatus);
            assert.equal(options.headers["X-Mail-Monitor-Scope"], jobScope);
            const id = decodeURIComponent(url.split("/").at(-1));
            return job(id, options);
        }});
    t.after(() => tracker.suspend());
    return {tracker, storage, calls, views, jobs: () => calls.filter(call => call.url.includes("/jobs/"))};
}
test("reload resumes exact reference with GET and retires it only after acceptance", async t => {
    const storage = memory();
    const before = harness(t, {storage});
    before.tracker.remember("send:synthetic");
    assert.equal(before.calls.length, 0);
    before.tracker.suspend();
    const after = harness(t, {storage});
    await after.tracker.resume();
    assert.equal(after.jobs().length, 1);
    assert.equal(after.jobs()[0].url, "/api/graph/jobs/send%3Asynthetic");
    assert.deepEqual(JSON.parse(storage.getItem(KEY)).entries, []);
    assert.equal(after.views.at(-1).entries[0].code, "accepted");
});
test("two pending references are not confused on navigation", async t => {
    const h = harness(t);
    h.tracker.remember("send:first"); h.tracker.remember("send:second");
    h.tracker.suspend();
    await h.tracker.resume();
    assert.deepEqual(h.jobs().map(call => call.url).sort(),
        ["/api/graph/jobs/send%3Afirst", "/api/graph/jobs/send%3Asecond"]);
});
for (const state of ["different_login", "expired_session", "unavailable_context"]) {
    test("no job ID is rendered or queried with " + state, async t => {
        const storage = memory(), first = harness(t, {storage});
        first.tracker.remember("send:private-reference");
        const second = harness(t, {storage, contextScope: state === "different_login" ? OTHER : SCOPE,
            contextStatus: state === "expired_session" ? 401 : state === "unavailable_context" ? 503 : 200});
        await second.tracker.resume();
        assert.equal(second.jobs().length, 0);
        assert.ok(second.views.every(view => view.entries.length === 0));
        assert.equal(Boolean(storage.getItem(KEY)), state === "unavailable_context");
    });
}
test("fresh page from another account discards old scoped references", async t => {
    const storage = memory(), first = harness(t, {storage});
    first.tracker.remember("send:old");
    const second = harness(t, {storage, scope: OTHER, contextScope: OTHER});
    await second.tracker.resume();
    assert.equal(second.jobs().length, 0);
    assert.ok(second.views.every(view => view.entries.length === 0));
    assert.equal(JSON.parse(storage.getItem(KEY)).scope, OTHER);
});
test("old BFCache page cannot remove a newer login's stored references", async t => {
    const storage = memory();
    const newer = harness(t, {storage, scope: OTHER, contextScope: OTHER});
    newer.tracker.remember("send:new");
    const older = harness(t, {storage, contextScope: OTHER});
    await older.tracker.resume();
    assert.equal(JSON.parse(storage.getItem(KEY)).entries[0].id, "send:new");
    assert.equal(older.jobs().length, 0);
});
test("a return keeps Retry-After observed before the old monitor finishes", async t => {
    let now = Date.now();
    const storage = memory(), first = harness(t, {storage, now: () => now,
        job: () => reply(null, 503, {"Retry-After": "60"})});
    await assert.rejects(first.tracker.waitForMailJob("send:delay", {
        waitFn: async () => first.tracker.suspend(),
    }), error => error.code === "suspended" && !error.jobId);
    const stored = JSON.parse(storage.getItem(KEY)).entries[0];
    assert.equal(stored.retryAt, now + 60000);
    const second = harness(t, {storage, now: () => now});
    await second.tracker.resume();
    assert.equal(second.jobs().length, 0);
    now += 61000;
    await second.tracker.resume();
    assert.equal(second.jobs().length, 1);
});
test("next_attempt_at beyond monitor window is preserved across reload", async t => {
    const now = Date.now(), storage = memory();
    const first = harness(t, {storage, job: id => reply({success: true, job: {
        id, status: "failed", will_retry: true, next_attempt_at: (now + 86400000) / 1000, result: {},
    }})});
    await assert.rejects(first.tracker.waitForMailJob("send:scheduled"), {code: "mail_scheduled"});
    const second = harness(t, {storage});
    await second.tracker.resume();
    assert.equal(second.jobs().length, 0);
    assert.equal(second.views.at(-1).entries[0].code, "mail_status_deferred");
});
test("expiry is not extended by remember and expires without queue calls", async t => {
    let now = Date.now();
    const h = harness(t, {now: () => now});
    h.tracker.remember("send:expiry");
    const saved = JSON.parse(h.storage.getItem(KEY)).entries[0];
    now += 1000;
    h.tracker.remember("send:expiry");
    assert.equal(JSON.parse(h.storage.getItem(KEY)).entries[0].expiresAt, saved.expiresAt);
    now += TTL;
    h.tracker.suspend();
    await h.tracker.resume();
    assert.equal(h.jobs().length, 0);
});
test("storage holds only opaque scope, reference and bounded timestamps", async t => {
    const h = harness(t), createdAt = Date.now();
    h.storage.setItem(KEY, JSON.stringify({scope: SCOPE, document: "private",
        entries: [{id: "send:minimal", createdAt, expiresAt: createdAt + TTL, retryAt: createdAt + 60000,
            recipient: "private@example.invalid", body: "private"}]}));
    await h.tracker.resume();
    const saved = JSON.parse(h.storage.getItem(KEY));
    assert.deepEqual(Object.keys(saved).sort(), ["entries", "scope"]);
    assert.deepEqual(Object.keys(saved.entries[0]).sort(), ["createdAt", "expiresAt", "id", "retryAt"]);
    assert.doesNotMatch(h.storage.getItem(KEY), /private/);
    assert.equal(h.jobs().length, 0);
});
for (const raw of ["null", "not-json", "x".repeat(16385), JSON.stringify({scope: SCOPE, entries: {}})]) {
    test("malformed reference store makes no job query", async t => {
        const h = harness(t); h.storage.setItem(KEY, raw);
        await h.tracker.resume();
        assert.equal(h.jobs().length, 0);
    });
}
test("storage failure leaves foreground consultation functional and warns about recovery", async t => {
    const storage = {getItem() {throw new Error();}, setItem() {throw new Error();}, removeItem() {throw new Error();}};
    const h = harness(t, {storage});
    assert.equal((await h.tracker.waitForMailJob("send:ephemeral")).id, "send:ephemeral");
    assert.ok(h.views.some(view => view.warning === "storage_unavailable"));
    assert.equal(h.jobs().length, 1);
});
test("limit preserves older references and does not cause any send", t => {
    const h = harness(t);
    for (let i = 0; i < LIMIT; i++) assert.equal(h.tracker.remember("send:" + i), true);
    assert.equal(h.tracker.remember("send:overflow"), false);
    assert.equal(JSON.parse(h.storage.getItem(KEY)).entries.length, LIMIT);
    assert.equal(h.calls.length, 0);
    assert.equal(h.views.at(-1).warning, "limit");
});
test("concurrent foreground and resume share one monitor", async t => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const h = harness(t, {job: async id => { await pending; return accepted(id); }});
    const foreground = h.tracker.waitForMailJob("send:single");
    const background = h.tracker.resume();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.jobs().length, 1);
    release();
    await Promise.all([foreground, background]);
});
test("late response from suspended page cannot confirm or expose the old job", async t => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const h = harness(t, {job: async id => {await pending; return accepted(id);}});
    const foreground = h.tracker.waitForMailJob("send:old");
    await new Promise(resolve => setImmediate(resolve));
    h.tracker.suspend();
    const viewsBefore = h.views.length;
    release();
    await assert.rejects(foreground, error => error.code === "suspended" && !error.jobId);
    assert.ok(h.views.slice(viewsBefore).every(view => view.entries.length === 0));
    assert.equal(JSON.parse(h.storage.getItem(KEY)).entries[0].id, "send:old");
});
test("removing a reference stops late confirmation from reappearing", async t => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const h = harness(t, {job: async id => {await pending; return accepted(id);}});
    const foreground = h.tracker.waitForMailJob("send:forgotten");
    await new Promise(resolve => setImmediate(resolve));
    h.tracker.forget("send:forgotten");
    release();
    await assert.rejects(foreground, {code: "suspended"});
    assert.deepEqual(h.views.at(-1).entries, []);
});
test("browser lifecycle hides old references before validating BFCache return", async () => {
    const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
    const storage = memory(), now = Date.now();
    storage.setItem(KEY, JSON.stringify({scope: SCOPE, entries: [
        {id: "send:browser-back", createdAt: now, expiresAt: now + TTL, retryAt: now + 60000},
    ]}));
    const elements = new Map(), windowEvents = new Map(), documentEvents = new Map(), timers = new Map();
    const element = () => ({textContent: "", hidden: true, children: [], handlers: new Map(),
        append(child) {this.children.push(child);}, replaceChildren() {this.children = [];},
        addEventListener(name, handler) {this.handlers.set(name, handler);}});
    const panel = element();
    elements.set("mail-followup", panel);
    const document = {hidden: false, getElementById: id => elements.get(id), createElement: element,
        addEventListener: (name, handler) => documentEvents.set(name, handler)};
    let currentScope = SCOPE, sequence = 0;
    const calls = [];
    const context = {document, sessionStorage: storage, __FILES_APP__: {mailMonitorScope: SCOPE},
        SensorpointMailJobMonitor: monitor, AbortController, Date,
        fetch: async (url, options) => {
            calls.push({url, options}); assert.equal(url, "/api/graph/monitor-context");
            return reply({success: true, scope: currentScope});
        },
        setTimeout: (fn, delay) => {timers.set(++sequence, {fn, delay}); return sequence;},
        clearTimeout: id => timers.delete(id), addEventListener: (name, handler) => windowEvents.set(name, handler)};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../src/web/static/js/mail-followup.js"), "utf8"), context);
    const text = () => panel.children.map(child => child.textContent).join(" ");
    await documentEvents.get("DOMContentLoaded")();
    await new Promise(resolve => setImmediate(resolve));
    assert.match(text(), /send:browser-back/);
    assert.match(text(), /Próxima consulta/);
    windowEvents.get("pagehide")();
    assert.equal(panel.hidden, true);
    assert.doesNotMatch(text(), /send:browser-back/);
    currentScope = OTHER;
    windowEvents.get("pageshow")({persisted: true});
    await new Promise(resolve => setImmediate(resolve));
    assert.match(text(), /sessão de acompanhamento mudou/);
    assert.doesNotMatch(text(), /send:browser-back/);
    assert.equal(storage.getItem(KEY), null);
    context.SensorpointMailFollowup.suspend();
    assert.ok(calls.every(call => !call.options.method && call.options.cache === "no-store"));
});
for (const status of [401, 403]) {
    test("session rejection " + status + " cannot reopen foreground dialog with old job reference", async t => {
        const h = harness(t, {job: () => reply(null, status)});
        await assert.rejects(h.tracker.waitForMailJob("send:old-account"),
            error => error.code === "session_changed" && !error.jobId);
        assert.deepEqual(h.views.at(-1).entries, []);
        assert.equal(h.storage.getItem(KEY), null);
    });
}
test("limit warning survives verification when new foreground job cannot be persisted", async t => {
    const now = Date.now();
    const h = harness(t, {job: id => reply({success: true, job: {id, status: "pending",
        next_attempt_at: (now + 86400000) / 1000, result: {}}})});
    for (let i = 0; i < LIMIT; i++) h.tracker.remember("send:old-" + i);
    await assert.rejects(h.tracker.waitForMailJob("send:overflow"), {code: "mail_scheduled"});
    assert.equal(h.views.at(-1).warning, "limit");
    assert.equal(JSON.parse(h.storage.getItem(KEY)).entries.length, LIMIT);
});
test("transient context failure does not interrupt foreground M03 GET retries", async t => {
    let contexts = 0, attempts = 0;
    const waits = [], h = harness(t, {contextStatus: () => ++contexts === 1 ? 503 : 200,
        job: id => ++attempts === 1 ? reply(null, 503, {"Retry-After": "2"}) : accepted(id)});
    h.tracker.remember("send:resilient");
    await h.tracker.resume();
    assert.equal(h.jobs().length, 0);
    const result = await h.tracker.waitForMailJob("send:resilient", {waitFn: async delay => waits.push(delay)});
    assert.equal(result.id, "send:resilient");
    assert.equal(h.views.at(-1).warning, null);
    assert.deepEqual(waits, [2000]);
    assert.equal(h.jobs().length, 2);
    await h.tracker.resume();
    assert.equal(contexts, 2);
    assert.equal(h.views.at(-1).entries[0].code, "accepted");
});
test("foreground network outages retain Portuguese diagnostic and exact reference", async t => {
    const h = harness(t, {contextStatus: 503, job: () => {throw new TypeError("offline");}});
    await assert.rejects(h.tracker.waitForMailJob("send:offline", {
        maxPolls: 1, waitFn: async () => {},
    }), error => error.code === "mail_status_timeout" && error.jobId === "send:offline" &&
        /não foi possível confirmar/.test(error.message) && !/context_unavailable/.test(error.message));
    assert.ok(h.calls.every(call => call.url.includes("/jobs/")));
});
test("normal suspension has its own message rather than claiming session change", async t => {
    const h = harness(t, {job: id => reply({success: true, job: {id, status: "pending", result: {}}})});
    await assert.rejects(h.tracker.waitForMailJob("send:mobile", {
        waitFn: async () => h.tracker.suspend(),
    }), error => error.code === "suspended" && /continua quando voltar/.test(error.message) &&
        !/sessão.*mudou/.test(error.message) && !error.jobId);
});
test("old remember and rejected foreground never replace newer scope storage", async t => {
    const storage = memory();
    harness(t, {storage, scope: OTHER, contextScope: OTHER}).tracker.remember("send:newer");
    const snapshot = storage.getItem(KEY);
    const old = harness(t, {storage, contextScope: OTHER, job: () => reply(null, 403)});
    old.tracker.remember("send:old-page");
    assert.equal(storage.getItem(KEY), snapshot);
    await assert.rejects(old.tracker.waitForMailJob("send:old-page"), {code: "session_changed"});
    assert.equal(storage.getItem(KEY), snapshot);
});
test("POST response scope lets old HTML preserve current-session references and new ID", async t => {
    const storage = memory(), now = Date.now();
    harness(t, {storage, scope: OTHER, contextScope: OTHER}).tracker.remember("send:existing-new-session");
    const old = harness(t, {storage, contextScope: OTHER, jobScope: OTHER,
        job: id => reply({success: true, job: {id, status: "pending",
            next_attempt_at: (now + 86400000) / 1000, result: {}}})});
    old.tracker.remember("send:new-post", OTHER);
    assert.equal(JSON.parse(storage.getItem(KEY)).scope, OTHER);
    await assert.rejects(old.tracker.waitForMailJob("send:new-post"), {code: "mail_scheduled"});
    assert.deepEqual(JSON.parse(storage.getItem(KEY)).entries.map(entry => entry.id),
        ["send:existing-new-session", "send:new-post"]);
    assert.equal(old.jobs().length, 1);
});
