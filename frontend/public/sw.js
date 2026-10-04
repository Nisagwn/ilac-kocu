/* İlaç Koçu service worker.
 *
 * GİZLİLİK KURALI: Bu SW sağlık verisi içeren hiçbir yanıtı önbelleğe ALMAZ.
 *  - `/api/` altındaki her istek (proxy dahil: ölçümler, dozlar, koç sohbeti, uyarılar)
 *    doğrudan ağa gider; Cache Storage'a asla yazılmaz. Paylaşılan/kaybolan bir cihazda
 *    önbellekten kişisel sağlık verisi okunamamalıdır.
 *  - Sayfa (navigasyon) yanıtları da sunucuda kişiye özel render edildiği için önbelleğe
 *    alınmaz; ağ yoksa yalnızca statik `/offline.html` gösterilir.
 *  - Önbelleğe alınan tek şey kişisel veri içermeyen statik kabuktur (çevrimdışı sayfa,
 *    ikonlar, manifest) ve içerik-hash'li `/_next/static/` dosyalarıdır.
 */

const VERSION = "v1";
const SHELL_CACHE = `ik-shell-${VERSION}`;
const STATIC_CACHE = `ik-static-${VERSION}`;
// ServiceWorkerRegister geliştirme modunda "/sw.js?dev=1" ile kaydeder: hash'siz dev dosyaları önbelleğe alınmaz.
const DEV = new URL(self.location.href).searchParams.has("dev");
const OFFLINE_URL = "/offline.html";
const SHELL_ASSETS = [
  OFFLINE_URL,
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/badge-72.png",
];

// Önbellekte çevrimdışı sayfa bulunamazsa (ör. kurulum sırasında yönlendirildiyse) kullanılacak yedek.
const OFFLINE_FALLBACK_HTML =
  '<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>Çevrimdışı · İlaç Koçu</title><body style="font-family:system-ui,sans-serif;padding:24px;max-width:420px;margin:auto">' +
  "<h1>İnternet bağlantısı yok</h1><p>Bağlantınız geri geldiğinde yeniden deneyin.</p>" +
  "<p><strong>Acil bir durumda 112'yi arayın.</strong></p>" +
  '<button onclick="location.reload()">Yeniden dene</button></body></html>';

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      await Promise.all(
        SHELL_ASSETS.map(async (url) => {
          try {
            const res = await fetch(url, { cache: "reload", redirect: "manual" });
            // Yönlendirilmiş/başarısız yanıtları (ör. giriş sayfasına yönlendirme) saklama.
            if (res.ok && res.type === "basic") await cache.put(url, res);
          } catch {
            /* ağ hatası: kurulum yine de tamamlansın */
          }
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([SHELL_CACHE, STATIC_CACHE]);
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith("ik-") && !keep.has(k)).map((k) => caches.delete(k)));
      if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
      await self.clients.claim();
    })(),
  );
});

async function offlineResponse() {
  const cached = await caches.match(OFFLINE_URL);
  return (
    cached ??
    new Response(OFFLINE_FALLBACK_HTML, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Sağlık verisi: /api/ yanıtları ASLA önbelleğe alınmaz ve SW tarafından ele alınmaz.
  if (url.pathname.startsWith("/api/")) return;

  // Sayfa geçişleri: önce ağ (önbelleğe yazmadan), ağ yoksa çevrimdışı sayfa.
  if (req.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const preload = await event.preloadResponse;
          if (preload) return preload;
          return await fetch(req);
        } catch {
          return offlineResponse();
        }
      })(),
    );
    return;
  }

  // İçerik-hash'li Next.js statik dosyaları: önce önbellek (değişmezler, kişisel veri içermez).
  if (!DEV && url.pathname.startsWith("/_next/static/")) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(STATIC_CACHE);
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })(),
    );
    return;
  }

  // Kabuk dosyaları (ikonlar, manifest, offline): önce ağ, olmazsa önbellek.
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith(fetch(req).catch(async () => (await caches.match(url.pathname)) ?? Response.error()));
  }
  // Diğer her şey: tarayıcının varsayılan davranışı (SW karışmaz).
});

// ---- Web Push ----
// Backend yükü: {"title": "...", "body": "...", "url": "/bugun"} (url, data.url olarak da gelebilir).
self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = { body: event.data.text() };
    }
  }
  const title = payload.title || "İlaç Koçu";
  const url = payload.url || (payload.data && payload.data.url) || "/bugun";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || "İlaç zamanı geldi.",
      icon: payload.icon || "/icons/icon-192.png",
      badge: "/icons/badge-72.png",
      tag: payload.tag || "ilac-kocu",
      renotify: Boolean(payload.tag),
      lang: "tr",
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/bugun", self.location.origin);
  // Yalnızca kendi kökenimize yönlendir (yükteki URL'ye körü körüne güvenme).
  const href = target.origin === self.location.origin ? target.href : new URL("/bugun", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin && "focus" in client) {
          await client.focus();
          if ("navigate" in client && client.url !== href) await client.navigate(href).catch(() => {});
          return;
        }
      }
      await self.clients.openWindow(href);
    })(),
  );
});
