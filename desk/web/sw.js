/* GROMO service worker: app shell cache + Web Push. Never caches /api. */
const CACHE = 'desk-v4';
const SHELL = ['./', 'index.html', 'app.css', 'app.js', 'chart.js', 'share.js', 'manifest.json', 'icons/favicon.svg', 'icons/icon-180.png', 'icons/icon-192.png', 'icons/glyph.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // network first, shell from cache when offline
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match('index.html'))));
});

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  const title = d.title || 'GROMO';
  const opts = {
    body: d.body || d.text || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    tag: d.tag || d.mint || undefined,
    data: { url: d.url || (d.mint ? './#/coin/' + encodeURIComponent(d.mint) : './#/activity') },
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || './';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
