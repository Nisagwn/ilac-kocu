# Frontend

Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn/ui (base-ui) + Recharts. Mobil öncelikli PWA; arayüz metinleri Türkçe.

## Geliştirme

```bash
cp .env.example .env.local   # API_URL=http://localhost:8000 (FastAPI)
npm ci
npm run dev                  # http://localhost:3000
```

Kontroller (CI ile aynı): `npm run lint`, `npx tsc --noEmit`, `npm run build`.

Docker ile tüm yığın: depo kökünde `docker compose up --build` → web `:3000`, api `:8000`.
`web` servisi `API_URL=http://api:8000` ile çalışır; imaj `output: "standalone"` derlemesini root olmayan kullanıcıyla koşturur.

## Ekranlar

Giriş: `/giris`. Girişten sonra middleware kullanıcıyı rolünün ana sayfasına yönlendirir.

**Hasta (mobil öncelikli)**
- `/bugun` — günün dozları, tek dokunuşla "aldım / almadım" → `GET /patients/{id}/doses/today`, `PUT /patients/{id}/doses`
- `/olcum` — kan şekeri / tansiyon girişi ve 30 günlük grafik → `/patients/{id}/measurements`
- `/koc` — koç sohbeti; acil yanıtlar kırmızı kartla (112), kaynaklar yanıtın altında → `/coach/chat`, `/coach/history`
- `/ilaclar` — ilaç planı yönetimi, openFDA bilgi kartı → `/patients/{id}/medications`, `/drugs/label`
- `/ozet` — haftalık özet → `/patients/{id}/summary`

**Hekim (masaüstü)**
- `/hekim` — atanmış hasta tablosu: uyum bandı (iyi/orta/düşük), açık kritik uyarı sayısı → `GET /patients`
- `/hekim/hasta/[id]` — 30 günlük ölçüm eğrileri, doz takvimi ısı haritası, uyarılar, haftalık özet
- `/hekim/uyarilar` — açık uyarılar ve onaylama → `GET /alerts`, `POST /alerts/{id}/ack`

**Bakım veren (salt okunur)**
- `/yakin` — bağlı hastaların listesi
- `/yakin/hasta/[id]` — bağlı hastanın doz, ölçüm ve özet görünümü (değişiklik yapılamaz)

Rol alanları (`/bugun…`, `/hekim…`, `/yakin…`) `src/middleware.ts` içinde yalnızca kullanıcı deneyimi için ayrılır;
asıl yetki denetimi her istekte backend'de JWT ile yapılır (yetkisiz erişim 404).

## Kimlik doğrulama tasarımı

- `POST /api/auth/login` (Next.js route handler) FastAPI'den JWT alır ve **httpOnly**, `SameSite=Lax`
  (üretimde `Secure`) çereze yazar. JWT hiçbir zaman tarayıcı JavaScript'ine, `localStorage`'a veya yanıt gövdesine düşmez;
  XSS ile token çalınamaz. Rol çerezi yalnızca yönlendirme içindir.
- Tarayıcı tüm API çağrılarını `/api/proxy/<yol>` üzerinden yapar (`src/lib/api.ts`). Proxy
  (`src/app/api/proxy/[...path]/route.ts`) çerezdeki token'ı `Authorization: Bearer` başlığına ekleyip
  isteği `API_URL`'ye iletir. Böylece backend adresi tarayıcıya açılmaz ve CORS gerekmez.
- 401 yanıtında istemci `/giris`'e yönlendirir; `POST /api/auth/logout` çerezleri siler.

## PWA ve web push

- `public/manifest.webmanifest` — "İlaç Koçu", `start_url: /bugun`, `display: standalone`, tema `#0f766e`;
  ikonlar `public/icons/` (192/512, maskable 192/512, apple-touch-icon, bildirim rozeti).
- `public/sw.js` — service worker:
  - Sayfa geçişleri **önce ağ**; ağ yoksa `public/offline.html` (Türkçe, 112 hatırlatmalı).
  - **`/api/` yanıtları asla önbelleğe alınmaz**, kişiye özel sayfa HTML'i de saklanmaz (sağlık verisi gizliliği).
    Yalnızca kişisel veri içermeyen kabuk (offline sayfası, ikonlar, manifest) ve içerik-hash'li `/_next/static/` önbelleğe alınır.
  - `push` → bildirim (`title`, `body`, `url`); `notificationclick` → açık pencereyi odaklar / `url`'yi açar (yalnızca aynı köken).
  - Geliştirmede `/sw.js?dev=1` olarak kaydedilir ve statik dosyaları önbelleğe almaz.
- `src/components/push-toggle.tsx`:
  - `ServiceWorkerRegister` — kök layout'ta bir kez yerleştirilir, SW'yi kaydeder.
  - `PushToggle` — "İlaç hatırlatmalarını aç/kapat" kartı. Backend sözleşmesi (proxy üzerinden):
    `GET /push/vapid-public-key` → `{public_key}`, `POST /push/subscribe` `{endpoint, keys:{p256dh, auth}}`,
    `DELETE /push/subscribe` `{endpoint}`, `POST /push/test` → `{sent}`. Desteklenmeyen tarayıcı, reddedilmiş izin
    ve sunucuda VAPID yapılandırılmamış (503) durumlarını gösterir.
- Push için backend `.env`'inde `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` gerekir
  (`npx web-push generate-vapid-keys`). Bildirim içeriğinde sağlık verisi (değer, ilaç dozu) yer almamalıdır.
- **iOS:** web push yalnızca ana ekrana eklenmiş uygulamada çalışır (iOS 16.4+). Push ve SW için güvenli bağlam
  gerekir: `localhost` veya HTTPS.

## Güvenlik başlıkları

`next.config.ts` tüm yanıtlara `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Frame-Options: DENY` ve kısıtlı bir `Permissions-Policy` ekler; `/sw.js` `no-cache` ve `Service-Worker-Allowed: /` ile sunulur.
`src/middleware.ts` her sayfa isteğinde nonce'lu CSP kurar (`script-src 'self' 'nonce-…' 'strict-dynamic'`); ayrıntı `docs/M3/guvenlik-ve-pwa.md` §3.1.
