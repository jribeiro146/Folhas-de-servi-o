// Filled by /service-worker.js from the admitted release identity.
const RELEASE_ID = "__SENSORPOINT_RELEASE_SHA256__";
const BUILD_VERSION = RELEASE_ID.slice(0, 16);
const CACHE_PREFIX = "sensorpoint-service-static-";
const CACHE_NAME = CACHE_PREFIX + RELEASE_ID;
const VERSION_QUERY = "?v=" + BUILD_VERSION;
const OFFLINE_PATH = "/static/offline.html";
const OFFLINE_URL = OFFLINE_PATH + VERSION_QUERY;
// Only immutable public package assets. Never cache arbitrary /static/ URLs,
// API responses, signed URLs, authenticated HTML, PDFs or document data.
const PUBLIC_PATHS = [
    "/manifest.webmanifest",
    "/static/css/field-app.css", "/static/css/document-editor.css",
    "/static/css/editing-state.css", "/static/css/auth.css",
    "/static/css/maintenance.css", "/static/css/admin.css",
    "/static/js/document-editor.js", "/static/js/document-validation.js",
    "/static/js/editing-coordinator.js", "/static/js/mail-job-monitor.js",
    "/static/js/mail-followup.js", "/static/js/maintenance-model.js",
    "/static/js/maintenance-editor.js", "/static/js/admin.js", "/static/js/pwa.js",
    "/static/img/sensorpoint-logo.png", "/static/img/pwa/icon-192.png",
    "/static/img/pwa/icon-512.png", "/static/img/pwa/icon-maskable-512.png",
    OFFLINE_PATH,
];
const APP_SHELL = PUBLIC_PATHS.map(path => path + VERSION_QUERY);
const validResponse = (response, identity) => response?.ok && response.type === "basic" &&
    !response.redirected && response.headers.get("X-App-Version") === identity;
const unavailable = () => new Response("Recurso indisponível nesta versão sem ligação. Guarde o trabalho antes de atualizar.", {
    status: 503, headers: {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
});

self.addEventListener("install", event => {
    event.waitUntil((async () => {
        if (!/^[a-f0-9]{64}$/.test(RELEASE_ID)) throw new Error("Unbound worker identity");
        // Validate every response before writing; a deployment halfway through
        // installation must not populate this release's cache with other code.
        const resources = await Promise.all(APP_SHELL.map(async url => {
            const response = await fetch(url, {cache: "no-store", redirect: "error"});
            if (!validResponse(response, RELEASE_ID)) throw new Error("Asset release mismatch");
            const storedResponse = response.clone();
            // Drain the network body before awaiting every asset: large bodies
            // must not hold all HTTP/1 connections while later fetches queue.
            await response.arrayBuffer();
            return [url, storedResponse];
        }));
        const cache = await caches.open(CACHE_NAME);
        try {
            for (const [url, response] of resources) await cache.put(url, response);
        } catch (error) {
            await caches.delete(CACHE_NAME);
            throw error;
        }
        // No skipWaiting: an update waits for the old controlled tabs to close.
    })());
});

self.addEventListener("activate", event => {
    event.waitUntil((async () => {
        for (const key of await caches.keys()) {
            if (key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME) await caches.delete(key);
        }
        // No clients.claim: do not take over an already-open document.
    })());
});

self.addEventListener("fetch", event => {
    const request = event.request;
    if (request.method !== "GET") return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith("/api/")) {
        event.respondWith(fetch(request));
        return;
    }
    if (request.mode === "navigate" || ["/", "/login", "/logout"].includes(url.pathname)) {
        event.respondWith(fetch(request).catch(async () => {
            const cache = await caches.open(CACHE_NAME);
            const fallback = await cache.match(OFFLINE_URL);
            return validResponse(fallback, RELEASE_ID) ? fallback : unavailable();
        }));
        return;
    }
    if (!PUBLIC_PATHS.includes(url.pathname) || !url.searchParams.has("v")) return;
    event.respondWith((async () => {
        const version = url.searchParams.get("v");
        if (!/^[a-f0-9]{16}$/.test(version) || url.search !== "?v=" + version) return unavailable();
        if (version === BUILD_VERSION) {
            const cache = await caches.open(CACHE_NAME);
            const cached = await cache.match(request); // Exact URL, including ?v=.
            if (validResponse(cached, RELEASE_ID)) return cached;
        }
        try {
            const response = await fetch(request, {cache: "no-store", redirect: "error"});
            const identity = response.headers.get("X-App-Version") || "";
            if (!/^[a-f0-9]{64}$/.test(identity) || identity.slice(0, 16) !== version ||
                !validResponse(response, identity)) return unavailable();
            if (identity === RELEASE_ID) {
                const copy = response.clone();
                event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(request, copy)).catch(() => {}));
            }
            // A new page can use the new server while an old worker is waiting
            // to retire, but new assets never enter the old release's cache.
            return response;
        } catch (_) { return unavailable(); }
    })());
});
