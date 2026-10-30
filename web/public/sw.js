// track-seeds: оболочка сайта в кеше — приложение с экрана «Домой» открывается сразу, без связи — не пустой экран.
// Данные (/api/) не кешируются никогда: дробилки — только живые. Новая версия сайта подхватывается сама:
// страницы — сначала из сети, файлы /_next/static/ неизменны (в имени — хеш содержимого).
const VERSION = "track-seeds-v1";
const STATIC = `${VERSION}-static`;
const PAGES = `${VERSION}-pages`;
const MAX_STATIC = 200;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(PAGES)
      .then((cache) => cache.addAll(["/", "/login/"]))
      .catch(() => {})
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cache) {
  const keys = await cache.keys();
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_STATIC))) await cache.delete(key);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(
      caches.open(STATIC).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        const response = await fetch(request);
        if (response.ok) {
          await cache.put(request, response.clone());
          trim(cache);
        }
        return response;
      }),
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then(async (response) => {
          if (response.ok) (await caches.open(PAGES)).put(request, response.clone());
          return response;
        })
        .catch(async () => (await caches.match(request)) || (await caches.match("/")) || Response.error()),
    );
    return;
  }

  // значки, манифест: из кеша сразу, обновляем в фоне
  event.respondWith(
    caches.open(STATIC).then(async (cache) => {
      const hit = await cache.match(request);
      const fresh = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => hit || Response.error());
      return hit || fresh;
    }),
  );
});
