const CACHE = "spotify-easy-tool-v48-endpoint-fallback-fix";
const FILES = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  if (
    url.hostname === "api.spotify.com" ||
    url.hostname === "accounts.spotify.com" ||
    url.hostname === "api-partner.spotify.com"
  ) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (url.origin === location.origin) {
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
  }
});
