const CACHE_NAME = "folhas-servico-shell-v5";
const APP_SHELL = [
    "/manifest.webmanifest",
    "/static/css/field-app.css",
    "/static/js/field-app.js"
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

    if (isNavigationRequest && isSameOrigin) {
        event.respondWith(
            fetch(event.request)
                .then((response) => response)
                .catch(() => caches.match("/"))
        );
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
});
