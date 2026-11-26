/* Service worker du hub : l'appli s'installe sur l'écran d'accueil.
   Réseau d'abord (le hub dépend de la session Authentik), fichiers du design en cache.
   Change VERSION à chaque mise en ligne qui touche aux fichiers statiques. */
const VERSION = "hub-1";
const STATIQUES = ["/static/encre/encre.css", "/static/encre/verre.css", "/static/encre/encre.js",
  "/static/encre/polices.css", "/static/hub.css", "/static/hub.js", "/static/icone-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(STATIQUES)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((cles) => Promise.all(cles.filter((k) => k !== VERSION).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (!url.pathname.startsWith("/static/")) return;   // pages et /api : toujours le réseau
  e.respondWith(
    caches.match(e.request, { ignoreSearch: true }).then((enCache) => {
      const reseau = fetch(e.request).then((r) => {
        if (r.ok) caches.open(VERSION).then((c) => c.put(e.request, r.clone()));
        return r;
      }).catch(() => enCache);
      return enCache || reseau;
    })
  );
});
