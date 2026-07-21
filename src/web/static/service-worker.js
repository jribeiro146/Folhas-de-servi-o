const BUILD_VERSION = "20260721-sync-v2";
const CACHE_PREFIX = "sensorpoint-service-static-";
const CACHE_NAME = `${CACHE_PREFIX}${BUILD_VERSION}`;
const VERSION_QUERY = `?v=${encodeURIComponent(BUILD_VERSION)}`;
const OFFLINE_URL = `/static/offline.html${VERSION_QUERY}`;
const APP_SHELL = [
    `/manifest.webmanifest${VERSION_QUERY}`,
    `/static/css/field-app.css${VERSION_QUERY}`,
    `/static/css/document-editor.css${VERSION_QUERY}`,
    `/static/css/editing-state.css${VERSION_QUERY}`,
    `/static/css/auth.css${VERSION_QUERY}`,
    `/static/js/document-editor.js${VERSION_QUERY}`,
    `/static/js/editing-coordinator.js${VERSION_QUERY}`,
    `/static/js/pwa.js${VERSION_QUERY}`,
    `/static/img/sensorpoint-logo.png${VERSION_QUERY}`,
    `/static/img/pwa/icon-192.png${VERSION_QUERY}`,
    `/static/img/pwa/icon-512.png${VERSION_QUERY}`,
    `/static/img/pwa/icon-maskable-512.png${VERSION_QUERY}`,
    OFFLINE_URL,
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys
                    .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
                    .map((key) => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

const cachedVersion = async (request) => {
    const exact = await caches.match(request);
    if (exact) {
        return exact;
    }
    const requestUrl = new URL(request.url);
    const cache = await caches.open(CACHE_NAME);
    const keys = await cache.keys();
    const candidate = keys.find((key) => new URL(key.url).pathname === requestUrl.pathname);
    return candidate ? cache.match(candidate) : undefined;
};

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") {
        return;
    }

    const requestUrl = new URL(event.request.url);
    const sameOrigin = requestUrl.origin === self.location.origin;
    if (!sameOrigin) {
        return;
    }

    const isApiRequest = requestUrl.pathname.startsWith("/api/");
    const isAuthenticatedPage = ["/", "/login", "/logout"].includes(requestUrl.pathname);
    const isNavigation = event.request.mode === "navigate";
    const isVersionedAsset = requestUrl.searchParams.has("v") && (
        requestUrl.pathname.startsWith("/static/")
        || requestUrl.pathname === "/manifest.webmanifest"
    );

    if (isApiRequest) {
        event.respondWith(fetch(event.request));
        return;
    }

    if (isNavigation || isAuthenticatedPage) {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(OFFLINE_URL))
        );
        return;
    }

    if (!isVersionedAsset) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                if (response.ok && response.type === "basic") {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                }
                return response;
            })
            .catch(() => cachedVersion(event.request))
    );
});
