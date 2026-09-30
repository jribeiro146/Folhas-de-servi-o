"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/web/static/service-worker.js"), "utf8");

function worker({ online = false } = {}) {
    const listeners = new Map();
    const stores = new Map();
    const requested = [];
    const urlOf = value => new URL(typeof value === "string" ? value : value.url, "https://field.test").href;
    const caches = {
        async open(name) {
            if (!stores.has(name)) stores.set(name, new Map());
            const data = stores.get(name);
            return {
                async match(request) { return data.get(urlOf(request))?.clone(); },
                async put(request, response) { data.set(urlOf(request), response.clone()); },
                async keys() { return Array.from(data.keys(), url => ({ url })); },
                async addAll(urls) {
                    for (const url of urls) data.set(urlOf(url), new Response(url.includes("offline.html") ? "OFFLINE" : "ASSET"));
                },
            };
        },
        async keys() { return Array.from(stores.keys()); },
        async delete(name) { return stores.delete(name); },
        // Deliberately poisons any lookup across all caches: authenticated data
        // cached by an older release must never be served by the current worker.
        async match() { return new Response("OBSOLETE PRIVATE CONTENT"); },
    };
    vm.runInNewContext(source, {
        URL, Response, caches,
        self: {
            location: { origin: "https://field.test" },
            addEventListener(type, listener) { listeners.set(type, listener); },
            async skipWaiting() {}, clients: { async claim() {} },
        },
        async fetch(request) {
            requested.push(request.url);
            if (!online) throw new TypeError("Connection unavailable");
            return new Response("NETWORK");
        },
    });
    async function lifecycle(type) {
        const pending = [];
        listeners.get(type)({ waitUntil(promise) { pending.push(promise); } });
        await Promise.all(pending);
    }
    function request(url, extras = {}) {
        let response;
        const pending = [];
        listeners.get("fetch")({
            request: { method: "GET", mode: "same-origin", url: urlOf(url), ...extras },
            respondWith(value) { response = Promise.resolve(value); },
            waitUntil(value) { pending.push(value); },
        });
        return { response, pending };
    }
    return { lifecycle, request, stores, requested };
}

test("API changes cannot be replaced with stale cached success while offline", async () => {
    const instance = worker();
    await instance.lifecycle("install");
    await assert.rejects(instance.request("/api/files").response, /Connection unavailable/);
    assert.equal(instance.request("/api/file/example", { method: "POST" }).response, undefined);
});

test("offline navigation uses the current public fallback and never old cached pages", async () => {
    const instance = worker();
    await instance.lifecycle("install");
    const response = await instance.request("/", { mode: "navigate" }).response;
    assert.equal(await response.text(), "OFFLINE");
    const protectedPage = await instance.request("/demo/maintenance/example", { mode: "navigate" }).response;
    assert.equal(await protectedPage.text(), "OFFLINE");
});

test("offline assets fall back only within current cache; missing assets produce 503", async () => {
    const instance = worker();
    await instance.lifecycle("install");
    const cached = await instance.request("/static/css/field-app.css?v=another-build").response;
    assert.equal(await cached.text(), "ASSET");
    const absent = await instance.request("/static/missing.js?v=current").response;
    assert.equal(absent.status, 503);
    assert.equal(instance.request("https://outside.test/static/x.js?v=1").response, undefined);
});

test("online navigation always goes to server and is not cached", async () => {
    const instance = worker({ online: true });
    await instance.lifecycle("install");
    const before = Array.from(instance.stores.values()).reduce((count, cache) => count + cache.size, 0);
    const response = await instance.request("/", { mode: "navigate" }).response;
    assert.equal(await response.text(), "NETWORK");
    assert.equal(Array.from(instance.stores.values()).reduce((count, cache) => count + cache.size, 0), before);
});

test("missing offline cache still returns a valid unavailable response", async () => {
    const instance = worker();
    const response = await instance.request("/", { mode: "navigate" }).response;
    assert.equal(response.status, 503);
});
