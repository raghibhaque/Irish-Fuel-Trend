// Irish Fuel Trend — service worker.
//
// Strategy:
//   * HTML navigations  → network-first, cache fallback.
//     Users on flaky connections still see the last-loaded page instead of
//     the Chrome dino, but a live network gets fresh markup + fresh
//     ?v= query strings on assets.
//   * /data/*.json      → stale-while-revalidate.
//     Instant paint from cache, background fetch updates for the next visit.
//     Freshness comes from `manifest.json.generated_at`, not from HTTP cache.
//   * Same-origin static → cache-first (asset URLs already carry ?v= busters,
//     so a new deploy invalidates cleanly without SW gymnastics).
//   * Cross-origin (fonts, chart.js CDN) → pass-through, don't touch.
//
// Bump SW_VERSION on any change here — activate handler purges old caches.

const SW_VERSION = "ift-v1";
const CACHE = `ift-${SW_VERSION}`;

// Precache the app shell so the first offline load has something to render.
// Query strings intentionally match what the HTML references — a mismatch
// means the SW cache and the browser HTTP cache both miss, defeating the
// point. Keep this in sync with the ?v= tags in index.html / county.html.
const PRECACHE = [
    "./",
    "./index.html",
    "./county.html",
    "./style.css?v=12",
    "./shared.js?v=10",
    "./app.js?v=17",
    "./county.js?v=8",
    "./manifest.webmanifest",
    "./icon.svg",
    "./icon-maskable.svg",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()),
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
            .then(() => self.clients.claim()),
    );
});

// Small helper — cache a response only if it's a real 200 same-origin one.
// Redirects and opaque cross-origin responses are dangerous to persist.
function cacheable(response) {
    return response && response.status === 200 && response.type === "basic";
}

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;

    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return; // let browser handle CDN/fonts

    // HTML navigations: network-first.
    if (req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html")) {
        event.respondWith(
            fetch(req)
                .then((res) => {
                    if (cacheable(res)) {
                        const copy = res.clone();
                        caches.open(CACHE).then((c) => c.put(req, copy));
                    }
                    return res;
                })
                .catch(() => caches.match(req).then(hit => hit || caches.match("./index.html"))),
        );
        return;
    }

    // /data/*.json: stale-while-revalidate.
    if (url.pathname.includes("/data/") && url.pathname.endsWith(".json")) {
        event.respondWith(
            caches.open(CACHE).then((cache) =>
                cache.match(req).then((cached) => {
                    const network = fetch(req).then((res) => {
                        if (cacheable(res)) cache.put(req, res.clone());
                        return res;
                    }).catch(() => cached);
                    return cached || network;
                }),
            ),
        );
        return;
    }

    // Same-origin static (JS/CSS/SVG): cache-first.
    event.respondWith(
        caches.match(req).then((cached) => cached || fetch(req).then((res) => {
            if (cacheable(res)) {
                const copy = res.clone();
                caches.open(CACHE).then((c) => c.put(req, copy));
            }
            return res;
        })),
    );
});
