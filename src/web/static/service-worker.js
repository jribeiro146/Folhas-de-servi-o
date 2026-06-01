const CACHE_NAME = "sensorpoint-service-pwa-v1";
const APP_SHELL = [
    "/manifest.webmanifest",
    "/static/css/field-app.css",
    "/static/css/document-editor.css",
    "/static/css/auth.css",
    "/static/js/document-editor.js",
    "/static/js/pwa.js",
    "/static/img/sensorpoint-logo.png",
    "/static/img/pwa/icon-192.png",
    "/static/img/pwa/icon-512.png",
    "/static/img/pwa/icon-maskable-512.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
            .catch(() => null)
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") {
        return;
    }

    const requestUrl = new URL(event.request.url);
    const isNavigationRequest = event.request.mode === "navigate";
    const isSameOrigin = requestUrl.origin === self.location.origin;
    const isApiRequest = isSameOrigin && requestUrl.pathname.startsWith("/api/");
    const isCacheableAsset = isSameOrigin && (
        requestUrl.pathname.startsWith("/static/") ||
        requestUrl.pathname === "/manifest.webmanifest" ||
        requestUrl.pathname === "/service-worker.js"
    );

    if (isNavigationRequest && isSameOrigin) {
        event.respondWith(
            fetch(event.request)
                .then((response) => response)
                .catch(() => caches.match("/"))
        );
        return;
    }

    if (isApiRequest) {
        event.respondWith(fetch(event.request));
        return;
    }

    if (isCacheableAsset) {
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) => (
                cache.match(event.request).then((cached) => {
                    const networkFetch = fetch(event.request)
                        .then((response) => {
                            if (response.ok) {
                                cache.put(event.request, response.clone());
                            }
                            return response;
                        })
                        .catch(() => cached);

                    return cached || networkFetch;
                })
            ))
        );
    }
});
