# API platform katmanı

Kod: `backend/app/middleware.py` (saf ASGI middleware'ler), `backend/app/services/ratelimit.py` (token bucket), `backend/app/services/idempotency.py`, `backend/app/pagination.py`, `backend/app/routers/health.py`, `backend/app/routers/collections.py`. Testler: `backend/tests/test_platform.py`.

Middleware sırası (dıştan içe): istek kimliği/erişim logu → CORS → istek sınırı → sürüm başlığı → ETag → idempotency → rota.

## Sürümleme

- Tüm uçlar `/v1` altında. Frontend proxy'si (`frontend/src/app/api/proxy/[...path]/route.ts`) ve auth rotaları `API_BASE = ${API_URL}/v1` kullanır.
- Sürümsüz eski yollar (`/patients/...`) aynı router'lara bağlı kalır, OpenAPI şemasında görünmez ve `Deprecation: true`, `Link: </v1/...>; rel="successor-version"` başlıklarını taşır. Prometheus `route` etiketi sürümü korur (`/v1/auth/me` ile `/auth/me` ayrı sayılır): eski yolların kullanımı izlenip kaldırma zamanı belirlenebilir.
- Spec yolları: `GET /v1/medications|doses|measurements?patient_id=`. Yetki kuralları hasta-merkezli yollarla aynı (yetkisiz → 404).

## İstek sınırı (token bucket)

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `API_RATE_LIMIT_ENABLED` | `true` | Kapatılırsa başlık da eklenmez |
| `API_RATE_LIMIT_CAPACITY` | 120 | Kova boyutu (anlık patlama) |
| `API_RATE_LIMIT_REFILL_PER_SECOND` | 2.0 | Sürekli hız (dakikada 120 istek) |

- Kimlik: geçerli erişim token'ı varsa kullanıcı (`api:u:<id>`), yoksa istemci IP'si (`api:ip:<ip>`; `X-Forwarded-For` yalnızca `TRUSTED_PROXIES`'ten). Sahte token kullanıcı kovası açamaz, IP kovasına düşer.
- Redis'te tek Lua betiği (`TOKEN_BUCKET_LUA`) okur-doldurur-harcar-yazar; atomiktir ve saat olarak Redis'in `TIME`'ını kullanır, yani birden çok API kopyası aynı kovayı tutarlı paylaşır. Anahtar kova dolma süresi kadar sonra kendiliğinden silinir. Redis yoksa ya da hata verirse süreç içi kova (kopya başına).
- Her yanıtta `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (kovanın dolmasına saniye). Aşımda 429 + `Retry-After` (bir jeton için saniye).
- Muaf: `/health*`, `/metrics`, CORS ön kontrolü ve **koça mesaj gönderme** (`POST /coach/chat`, `/coach/jobs`). Acil belirti mesajına güvenlik yanıtı asla sınıra takılmamalı; bu uçların kendi hasta başına sınırı var ve o sınır acil mesajları muaf tutar.
- Giriş/kayıt uçlarındaki daha sıkı sabit pencere sınırları (`login_*`, `register_*`) ayrıca geçerlidir.

## Idempotency-Key

- POST/PUT/PATCH/DELETE isteğinde `Idempotency-Key` başlığı (8-255 karakter: harf, rakam, `- _ : .`; geçersizse 400). Yalnızca giriş yapmış kullanıcılar için; anahtar kullanıcı başına tekildir.
- İlk istek işlenir, yanıt (durum, gövde, `Content-Type`, `Location`) `idempotency_keys` tablosuna yazılır (migration `0008_idempotency`). Aynı anahtar + aynı istek (yöntem, yol, sorgu, gövde SHA-256) → saklanan yanıt + `Idempotent-Replayed: true`, işlem tekrarlanmaz. Aynı anahtar + farklı istek → 422. İlk istek sürerken gelen tekrar → 409 + `Retry-After: 1`.
- 4xx yanıtlar da saklanır (aynı hatalı istek aynı hatayı alır); 5xx ve `IDEMPOTENCY_MAX_BODY_BYTES`'tan büyük yanıtlar saklanmaz (kayıt silinir, istemci aynı anahtarla yeniden deneyebilir). Çöken süreçten kalan "işleniyor" kaydı 5 dk sonra devralınır.
- Gizlilik: saklanan gövde sağlık verisi içerebilir. Kayıtlar `IDEMPOTENCY_TTL_HOURS` (24) sonra `purge_expired` ile silinir, kullanıcı silinince de silinir (FK `ON DELETE CASCADE`).
- Frontend (`frontend/src/lib/api.ts`) her yazma isteğine `crypto.randomUUID()` anahtarı ekler; proxy bunu backend'e iletir.

## ETag ve önbellek

- Başarılı GET yanıtlarına gövdenin SHA-256 özetinden güçlü `ETag`; tüm GET yanıtlarına `Cache-Control: private, no-cache` (paylaşılan önbellekler saklamaz, tarayıcı her seferinde doğrular).
- `If-None-Match` eşleşirse (virgüllü liste, `W/` öneki ve `*` desteklenir) gövdesiz 304. Hasta verisi değişince özet değişir, 200 döner.
- Proxy `If-None-Match`'i iletir, `ETag`/`Cache-Control`'ü ve 304'ü tarayıcıya geri verir; tarayıcı yeniden doğrulamayı kendisi yapar (uygulama kodu değişmedi). Tarayıcıda doğrulandı: ikinci ziyarette ölçüm ve `/auth/me` istekleri 304 döndü.

## Cursor sayfalama

- Uçlar: ölçümler (artan zaman), doz kayıtları, hasta uyarıları (azalan), koç geçmişi (eskiye doğru sayfa, sayfa içinde eskiden yeniye).
- `limit` (1..200) ve `cursor`. Gövde geriye dönük uyumluluk için dizi olarak kalır; sonraki sayfa `X-Next-Cursor` ve `Link: <...>; rel="next"` başlıklarıyla bildirilir, son sayfada bu başlıklar yoktur. `limit` verilmezse eski davranış (pencere içindeki tümü).
- Cursor opak base64url JSON'dur: sıralama anahtarının son değeri + satır id'si. Keyset (`(anahtar, id) > (...)`) olduğundan araya kayıt girse de sayfa kaymaz; eşit zaman damgalı kayıtlar id ile tekilleşir. Bozuk cursor → 400.

## Sağlık uçları

| Uç | Ne zaman 200 | Kullanım |
|---|---|---|
| `GET /health/live` | Süreç yanıt veriyor | Yeniden başlatma kararı (bağımlılıklara bakmaz) |
| `GET /health/ready` | DB `SELECT 1`, Redis `PING` (tanımlıysa), veritabanı alembic head'de | Yük dengeleyiciye ekleme/çıkarma |
| `GET /health` | Eski canlılık ucu | Geriye dönük |

`/health/ready` gövdesi: `{"status": "ok"|"unavailable", "checks": {"database": "ok"|"error", "redis": "ok"|"error"|"disabled", "migrations": "ok"|"pending"|"missing"}}`. Bağlantı dizesi ya da hata metni döndürülmez. `HEALTH_CHECK_MIGRATIONS=false` şema kontrolünü kapatır.
