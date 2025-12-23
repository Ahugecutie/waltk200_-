/* Minimal offline-first service worker for PWA shell. */
const CACHE = "leadingstock-pwa-v1";
const ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/manifest.webmanifest",
  // Icons are optional; if missing, caching will ignore failures at runtime fetch.
  "/icon-192.png",
  "/icon-512.png",
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
          const shell = await caches.match("/index.html");
          if (shell) return shell;
        }
        throw new Error("offline");
      });
    })
  );
});


