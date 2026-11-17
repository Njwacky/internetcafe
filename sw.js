/* Café Desk Owner Console — service worker (network-first with offline fallback) */
const VERSION = 'cafedesk-console-v2';
const CORE = [
  '/dashboard',
  '/owner-auth.js',
  '/offline.html',
  '/manifest.webmanifest',
  '/assets/pwa/icon-192.png',
  '/assets/pwa/icon-512.png',
  '/assets/pwa/icon-maskable-192.png',
  '/assets/pwa/icon-maskable-512.png',
  '/assets/pwa/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(CORE).catch(() => undefined))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Never cache Supabase/API traffic — auth, realtime and REST must stay live.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/rest/') || url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/functions/') || url.pathname.startsWith('/storage/')) return;

  // Navigations: try the network, fall back to the cached shell, then offline page.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => undefined);
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match('/dashboard') || caches.match('/offline.html')))
    );
    return;
  }

  // Static assets: network-first with cache fallback so deploys stay fresh.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && (url.pathname.startsWith('/assets/') || url.pathname.endsWith('.webmanifest'))) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => undefined);
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'skip-waiting') self.skipWaiting();
});
