"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/js/pwa.js"), "utf8");

async function page({waiting = false, active = true, workspace = true} = {}) {
    const events = new Map(), registrationEvents = new Map(), workerEvents = new Map(), notices = [];
    let updates = 0, reloads = 0, takeovers = 0;
    const field = {value: "Trabalho sintético por guardar"};
    const candidate = {state: "installing", addEventListener: (type, fn) => workerEvents.set(type, fn)};
    const registration = {waiting: waiting ? candidate : null, active: active ? {} : null, installing: candidate,
        addEventListener: (type, fn) => registrationEvents.set(type, fn),
        update: async () => { updates++; }};
    const serviceWorker = {register: async (url, options) => {
        assert.equal(url, "/service-worker.js"); assert.equal(options.updateViaCache, "none");
        return registration;
    }, addEventListener: () => { takeovers++; }};
    const navigator = {serviceWorker, userAgent: "", standalone: false};
    vm.runInNewContext(source, {navigator, window: {navigator, addEventListener: (type, fn) => events.set(type, fn),
        location: {reload: () => reloads++}},
        document: {querySelectorAll: () => [], addEventListener: () => {},
            querySelector: () => workspace ? {prepend: element => notices.push(element)} : null,
            getElementById: id => notices.find(item => item.id === id),
            createElement: () => ({setAttribute(name, value) { this[name] = value; }})}});
    events.get("load")();
    await new Promise(resolve => setImmediate(resolve));
    return {field, notices, registration, found: () => registrationEvents.get("updatefound")(),
        installed() {candidate.state = "installed"; workerEvents.get("statechange")();},
        stats: () => ({updates, reloads, takeovers})};
}
test("waiting update informs the user without touching unsaved fields or forcing activation", async () => {
    const p = await page({waiting: true});
    assert.equal(p.notices.length, 1);
    assert.match(p.notices[0].textContent, /guardado no servidor/);
    assert.match(p.notices[0].textContent, /todas as abas/);
    assert.equal(p.notices[0].role, "status");
    assert.equal(p.field.value, "Trabalho sintético por guardar");
    assert.deepEqual(p.stats(), {updates: 1, reloads: 0, takeovers: 0});
});
test("newly installed update produces one notice even for repeated state events", async () => {
    const p = await page();
    p.found(); p.installed(); p.installed();
    assert.equal(p.notices.length, 1);
    assert.equal(p.field.value, "Trabalho sintético por guardar");
});
test("first installation and login without a workspace require no update banner", async () => {
    for (const config of [{active: false}, {workspace: false}]) {
        const p = await page(config); p.found(); p.installed();
        assert.equal(p.notices.length, 0);
        assert.equal(p.stats().reloads, 0);
    }
});

