/* Minimal offline-first service worker for PWA shell. */
const CACHE = "leadingstock-pwa-v5";
const ASSETS = [
  "/app/",
  "/app/index.html",
  "/app/style.css",
  "/app/app.js",
  "/app/manifest.webmanifest",
  "/app/sw.js",
  // Icons are optional; if missing, caching will ignore failures at runtime fetch.
  "/app/icon-192.png",
  "/app/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then(async (c) => {
      // Cache core assets; ignore icon failures.
      for (const url of ASSETS) {
        try {
          await c.add(url);
        } catch {}
      }
    })
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => (k === CACHE ? Promise.resolve() : caches.delete(k))))
    )
  );
  self.clients.claim();
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // Don't interfere with websocket
  if (req.url.startsWith("ws:") || req.url.startsWith("wss:")) return;

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).catch(async () => {
        // If offline and navigation, return cached shell.
        if (req.mode === "navigate") {
          const shell = await caches.match("/app/index.html");
          if (shell) return shell;
        }
        throw new Error("offline");
      });
    })
  );
});


