"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const template = fs.readFileSync(path.join(__dirname, "../../src/web/static/service-worker.js"), "utf8");
const A = "a".repeat(64), B = "b".repeat(64), V = A.slice(0, 16);
const prefix = "sensorpoint-service-static-";
function response(body, identity = A, options = {}) {
    const value = new Response(body, {status: options.status || 200, headers: {"X-App-Version": identity}});
    Object.defineProperties(value, {type: {value: options.type || "basic"}, redirected: {value: !!options.redirected}});
    value.clone = () => response(body, identity, options);
    return value;
}
function worker({identity = A, failPut = false, limitedConnections = false} = {}) {
    const listeners = new Map(), stores = new Map(), requested = [];
    const state = {online: true, server: A, redirected: false, type: "basic", mismatch: false, takeovers: 0};
    let connections = 0;
    const waitingForBody = [];
    const urlOf = value => new URL(typeof value === "string" ? value : value.url, "https://field.test").href;
    const caches = {
        async open(name) {
            if (!stores.has(name)) stores.set(name, new Map());
            const data = stores.get(name);
            return {
                async match(request) { return data.get(urlOf(request))?.clone(); },
                async put(request, value) {
                    if (failPut) throw new Error("Storage full");
                    data.set(urlOf(request), value.clone());
                },
            };
        },
        async keys() { return [...stores.keys()]; },
        async delete(name) { return stores.delete(name); },
        async match() { throw new Error("Cross-cache lookup forbidden"); },
    };
    vm.runInNewContext(template.replace("__SENSORPOINT_RELEASE_SHA256__", identity), {
        URL, Response, caches,
        self: {location: {origin: "https://field.test"},
            addEventListener(type, listener) { listeners.set(type, listener); },
            async skipWaiting() { state.takeovers++; }, clients: {async claim() { state.takeovers++; }},
        },
        async fetch(request, options) {
            const url = urlOf(request);
            requested.push({url, options});
            if (!state.online) throw new TypeError("Connection unavailable");
            if (limitedConnections && connections >= 3) await new Promise(resolve => waitingForBody.push(resolve));
            connections++;
            const value = response(url.includes("offline.html") ? "OFFLINE" : "NETWORK-" + state.server,
                state.mismatch && url.includes("pwa.js") ? B : state.server, state);
            const read = value.arrayBuffer.bind(value);
            value.arrayBuffer = async () => {
                const bytes = await read();
                connections--;
                waitingForBody.shift()?.();
                return bytes;
            };
            return value;
        },
    });
    async function lifecycle(type) {
        const pending = [];
        listeners.get(type)({waitUntil(promise) { pending.push(promise); }});
        await Promise.all(pending);
    }
    function request(url, extras = {}) {
        let result;
        const pending = [];
        listeners.get("fetch")({
            request: {method: "GET", mode: "same-origin", url: urlOf(url), ...extras},
            respondWith(value) { result = Promise.resolve(value); },
            waitUntil(value) { pending.push(value); },
        });
        return {response: result, pending};
    }
    return {lifecycle, request, stores, requested, state, urlOf};
}

test("installation binds all assets to one release without forcing takeover", async () => {
    const w = worker();
    await w.lifecycle("install");
    const cache = w.stores.get(prefix + A);
    assert.ok(cache.size >= 20);
    for (const url of cache.keys()) assert.equal(new URL(url).search, "?v=" + V);
    await w.lifecycle("activate");
    assert.equal(w.state.takeovers, 0);
    assert.ok(w.requested.every(item => item.options.cache === "no-store" && item.options.redirect === "error"));
});
test("installation consumes bodies before a limited connection pool stalls later assets", async () => {
    const w = worker({limitedConnections: true});
    let timer;
    try {
        await Promise.race([w.lifecycle("install"), new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("Unconsumed bodies exhausted the connection pool")), 1500);
        })]);
        assert.ok(w.stores.get(prefix + A).size >= 20);
    } finally { clearTimeout(timer); }
});
for (const fault of ["server", "mismatch", "redirected", "type"]) {
    test("installation rejects inconsistent or non-public responses: " + fault, async () => {
        const w = worker();
        w.state[fault] = {server: B, mismatch: true, redirected: true, type: "opaque"}[fault];
        await assert.rejects(w.lifecycle("install"), /mismatch/);
        assert.equal(w.stores.size, 0);
    });
}
test("unbound template and full storage cannot install a partial release", async () => {
    await assert.rejects(worker({identity: "__SENSORPOINT_RELEASE_SHA256__"}).lifecycle("install"), /Unbound/);
    const w = worker({failPut: true});
    await assert.rejects(w.lifecycle("install"), /Storage full/);
    assert.equal(w.stores.size, 0);
});
test("activation removes only old application caches", async () => {
    const w = worker();
    await w.lifecycle("install");
    w.stores.set(prefix + B, new Map());
    w.stores.set("unrelated", new Map());
    await w.lifecycle("activate");
    assert.deepEqual([...w.stores.keys()], [prefix + A, "unrelated"]);
    assert.equal(w.state.takeovers, 0);
});
test("API reads fail offline; mutations, PDFs, private files and other origins never use Cache API", async () => {
    const w = worker();
    await w.lifecycle("install");
    w.state.online = false;
    await assert.rejects(w.request("/api/files").response, /Connection unavailable/);
    for (const [url, options] of [
        ["/api/file/example", {method: "POST"}], ["/static/private.pdf?v=" + V, {}],
        ["/work-folder/private.png?v=" + V, {}], ["/api/files", {method: "DELETE"}],
        ["https://outside.test/static/js/pwa.js?v=" + V, {}], ["/static/js/pwa.js", {}],
    ]) assert.equal(w.request(url, options).response, undefined);
});
test("offline navigation has only a public fallback, with no cached authenticated HTML", async () => {
    const w = worker();
    await w.lifecycle("install");
    w.stores.set(prefix + "legacy", new Map([[w.urlOf("/"), response("PRIVATE")]]));
    w.state.online = false;
    for (const url of ["/", "/login", "/logout", "/demo/maintenance/example", "/private.pdf"]) {
        assert.equal(await (await w.request(url, {mode: "navigate"}).response).text(), "OFFLINE");
    }
});
test("offline assets require the exact release query, with no path-only fallback", async () => {
    const w = worker();
    await w.lifecycle("install");
    w.state.online = false;
    assert.equal((await w.request("/static/css/field-app.css?v=" + V).response).status, 200);
    for (const query of [B.slice(0,16), "invalid", V + "&x=1", V + "&v=" + V]) {
        assert.equal((await w.request("/static/css/field-app.css?v=" + query).response).status, 503);
    }
});
test("an old worker passes verified new-version assets online without contaminating its cache", async () => {
    const w = worker();
    await w.lifecycle("install");
    w.state.server = B;
    const req = w.request("/static/js/pwa.js?v=" + B.slice(0, 16), {mode: "no-cors"});
    assert.equal(await (await req.response).text(), "NETWORK-" + B);
    await Promise.all(req.pending);
    assert.equal(w.requested.at(-1).options.cache, "no-store");
    assert.equal(w.requested.at(-1).options.redirect, "error");
    assert.equal(w.stores.size, 1);
    assert.ok([...w.stores.get(prefix + A).keys()].every(url => url.endsWith("?v=" + V)));
    w.state.online = false;
    assert.equal((await w.request("/static/js/pwa.js?v=" + B.slice(0,16)).response).status, 503);
});
test("new bytes cannot be substituted for a missing asset of an old release", async () => {
    const w = worker();
    w.state.server = B;
    assert.equal((await w.request("/static/js/pwa.js?v=" + V).response).status, 503);
});
test("a wrong-identity cached asset is discarded and replaced only by a verified response", async () => {
    const w = worker();
    await w.lifecycle("install");
    const url = w.urlOf("/static/js/pwa.js?v=" + V);
    w.stores.get(prefix + A).set(url, response("POISON", B));
    const req = w.request(url);
    assert.equal(await (await req.response).text(), "NETWORK-" + A);
    await Promise.all(req.pending);
    assert.equal(w.stores.get(prefix + A).get(url).headers.get("X-App-Version"), A);
});
test("online navigation always uses the server without populating the cache", async () => {
    const w = worker();
    await w.lifecycle("install");
    const cache = w.stores.get(prefix + A), before = cache.size;
    assert.equal(await (await w.request("/", {mode: "navigate"}).response).text(), "NETWORK-" + A);
    assert.equal(cache.size, before);
});
test("missing or poisoned offline fallback returns a non-cacheable 503", async () => {
    const w = worker();
    w.state.online = false;
    assert.equal((await w.request("/", {mode: "navigate"}).response).status, 503);
    w.stores.get(prefix + A).set(w.urlOf("/static/offline.html?v=" + V), response("PRIVATE", B));
    const result = await w.request("/", {mode: "navigate"}).response;
    assert.equal(result.status, 503);
    assert.equal(result.headers.get("Cache-Control"), "no-store");
});

