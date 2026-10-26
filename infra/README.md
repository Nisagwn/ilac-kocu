# Altyapı: ters proxy, yük dengeleme, gözlemlenebilirlik, yük testi

## Mimari

```
                     ┌──────────────────────── docker compose iç ağı ───────────────────────────┐
 tarayıcı ──:80────► │ nginx :80  ──────────────► web (Next.js :3000)                            │
 (TLS açıksa :443)   │   güvenlik başlıkları, gzip,      │ sunucu tarafı proxy                   │
                     │   limit_req, XFF=$remote_addr     │ API_URL=http://nginx:8081             │
                     │                                   ▼                                       │
 127.0.0.1:8000 ───► │ nginx :8081 "iç API ağ geçidi" ──least_conn──► api-1 / api-2 / api-3 :8000│
 (Swagger, yalnız    │   X-Upstream=$upstream_addr                        │                      │
  yerel)             │                                                    ├──► db (pgvector)     │
                     │ worker (koç kuyruğu + zamanlayıcı, :9101/metrics) ─┴──► redis              │
                     │ [observability] prometheus ── grafana      jaeger ◄── OTLP (api, worker)  │
                     └──────────────────────────────────────────────────────────────────────────┘
```

- **Tek dış giriş noktası nginx.** `web` ve `api` host'a port açmaz (`expose` yalnızca iç ağ).
- **İç API ağ geçidi (:8081).** Next.js'in sunucu tarafı proxy'si API'ye doğrudan değil nginx üzerinden gider
  (`API_URL=http://nginx:8081`), böylece tarayıcıdan gelen her API çağrısı api kopyalarına dağıtılır. Aynı
  ağ geçidi host'ta yalnızca `127.0.0.1:8000`'e yayınlanır: Swagger `http://localhost:8000/docs`, "Try it out"
  dahil çalışır ve dış ağdan erişilemez. (Dış :80'de `/docs` yayınlanmadı: Swagger'ın göreli istekleri
  Next.js'e düşerdi ve API yüzeyi gereksiz yere dışarı açılırdı.)
- **Çoğaltma.** `api` için `deploy.replicas: ${API_REPLICAS:-3}`; `container_name` ve sabit host portu yok.
  `docker compose up -d --scale api=1` (ya da `API_REPLICAS=1`) ile tek kopyaya inilir.
- **Docker DNS ile dağıtım.** `resolver 127.0.0.11 valid=5s` + upstream'de `server api:8000 resolve`
  (nginx ≥ 1.27.3): `api` adı tüm kopyaların IP'lerine çözülür ve periyodik olarak yenilenir; ölçeklenen
  ya da yeniden başlayan kopyalar nginx yeniden yüklenmeden dağıtıma girer/çıkar.
- **Upstream ayarları.** `least_conn` (uzun koç/LLM istekleri tek kopyada birikmesin), `max_fails=3
  fail_timeout=10s` (pasif sağlık kontrolü), `keepalive 32`. Bağlantı hatası / 502-504'te istek bir sonraki
  kopyaya gider; POST/PATCH gibi idempotent olmayan istekler upstream'e gönderildikten sonra tekrarlanmaz.
- **X-Upstream başlığı.** Her yanıtta yanıtı veren upstream üyesi (`$upstream_addr`). :8081'de api kopyasının
  IP'sini, :80'de web konteynerini gösterir. Demo:
  ```sh
  for i in 1 2 3 4 5 6; do curl -s -o /dev/null -D - http://localhost:8000/health/ready | grep -i x-upstream; done
  # X-Upstream: 172.26.0.5:8000 / 172.26.0.8:8000 / 172.26.0.7:8000 / ...
  ```

### İstemci IP'si ve güven zinciri
1. Dış :80/:443'te istemcinin gönderdiği `X-Forwarded-For` **yok sayılır**, `$remote_addr` ile yeniden yazılır.
2. Next.js (`lib/server.ts clientIpFrom`) bu tek değeri API isteğine ekler.
3. :8081 ağ geçidi gelen `X-Forwarded-For`'u korur (yoksa `$remote_addr`).
4. Backend `TRUSTED_PROXIES` (compose: özel ağ aralıkları) içinden gelen isteklerde XFF'in en sağdaki değerine
   güvenir. api'nin host portu olmadığı için bu aralıklardan yalnızca compose iç ağındaki konteynerler gelebilir.

Doğrulandı: `X-Forwarded-For: 6.6.6.6` ile yapılan girişte Redis'teki giriş sınırı anahtarı
`login:ip:172.26.0.1` (gerçek bağlantı adresi) oldu, taklit edilen değer değil.

### Güvenlik başlıkları (`infra/nginx/snippets/security-headers.conf`)
nginx'te: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`.
Next.js'in aynı adlı başlıkları `proxy_hide_header` ile gizlenir (çift başlık olmaz). HSTS yalnızca TLS
sunucusunda.

**CSP nginx'te değil, Next.js'te** (`frontend/src/middleware.ts`): politika istek başına üretilen bir nonce
içerir ve Next.js bu nonce'u kendi `<script>` etiketlerine ekler; nginx sabit bir CSP üretemez. `proxy-web.conf`
Next.js'in `Content-Security-Policy` başlığını **gizlemez**, `security-headers.conf` CSP eklemez — çift CSP
olsaydı tarayıcı ikisini birden uygular ve nonce'suz politika sayfayı kırardı.
Sayfa politikası: `script-src 'self' 'nonce-<n>' 'strict-dynamic'` (`'unsafe-inline'` yok), `style-src 'self'
'unsafe-inline'` (Recharts/öznitelik stilleri), `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`,
`frame-ancestors 'none'`, `connect-src`/`worker-src`/`manifest-src 'self'`, `img-src 'self' data: blob:`.
Dış `/api/*` (Next route handler'ları) için `next.config.ts` `default-src 'none'; frame-ancestors 'none'` verir;
iç ağ geçidi :8081 tarayıcıya açık olmadığından orada CSP yoktur. Ayrıntı: `docs/M3/guvenlik-ve-pwa.md` §3.1.

### Diğer nginx ayarları
- gzip (`gzip_proxied any`, JSON/JS/CSS/SVG/manifest), `client_max_body_size 1m`, gövde/başlık zaman aşımları.
- IP başına `limit_req` 20 r/s, burst 40 (`/_next/static/` muaf). Hız `NGINX_RATE_LIMIT` ile değişir
  (şablon `templates/00-ratelimit.conf.template`). Aşımda 429.
- `X-Request-ID`: güvenli biçimdeki gelen değer korunur, yoksa nginx üretir; upstream'e iletilir, yanıtta döner,
  JSON erişim logunda yer alır. (Next.js proxy'si şu an bu başlığı API'ye iletmiyor; ağ geçidi yeni kimlik üretir.)
- Erişim logu JSON; **sorgu dizesi loglanmaz** (`$uri`), çünkü `?q=` gibi değerler sağlık verisi içerebilir.

## Graceful shutdown ve sağlık kontrolleri
- `uvicorn --timeout-graceful-shutdown 20 --timeout-keep-alive 65` (backend/Dockerfile), compose
  `stop_grace_period: 30s`: SIGTERM'de yeni bağlantı alınmaz, süren istekler biter, sonra süreç çıkar.
- Keep-alive: uvicorn 65 sn ve Next.js `KEEP_ALIVE_TIMEOUT=65000`, nginx upstream `keepalive_timeout 30s`'ten
  uzun: boştaki bağlantıyı önce nginx kapatır, "kapanmış bağlantıya istek" kaynaklı 502 oluşmaz.
- Kapanan kopya: bağlantı reddedilir → nginx isteği diğer kopyaya aktarır (`proxy_next_upstream error`),
  3 hatadan sonra kopya 10 sn devre dışı (`max_fails`); Docker DNS kaydı 5 sn içinde düşer.
- Healthcheck'ler: api `GET /health/live` (süreç canlılığı; DB/Redis/migration için `/health/ready` ayrıca var),
  web `/manifest.webmanifest`, nginx `/nginx-health`. nginx, api kopyaları `healthy` olunca başlar.
  nginx açık kaynak sürümünde aktif sağlık kontrolü yoktur; pasif kontrol (`max_fails`) + Docker DNS kullanılır.

## Portlar

| Host adresi | Hizmet | Değişken |
|---|---|---|
| `http://localhost` (0.0.0.0:80) | Uygulama (nginx → web) | `HTTP_PORT` |
| `https://localhost` (TLS açıksa) | Uygulama (TLS) | `HTTPS_PORT` |
| `http://localhost:8000/docs` (127.0.0.1) | İç API ağ geçidi: Swagger / OpenAPI, api×N | `API_GATEWAY_PORT` |
| `127.0.0.1:5433` | PostgreSQL (yalnız yerel) | `DB_PORT` |
| `http://localhost:9090` | Prometheus (`observability`) | `PROMETHEUS_PORT` |
| `http://localhost:3001` | Grafana, admin/admin (`observability`) | `GRAFANA_PORT`, `GRAFANA_ADMIN_PASSWORD` |
| `http://localhost:16686` | Jaeger arayüzü (`observability`) | `JAEGER_UI_PORT` |

80 doluysa: `HTTP_PORT=8088 docker compose up -d`. Next.js geliştirme sunucusu (`npm run dev`) varsayılan
`API_URL=http://localhost:8000` ile Docker'daki ağ geçidine bağlanmaya devam eder.

## Profiller

```sh
docker compose up -d --build                                   # temel yığın (nginx + api×3 + web + worker)
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 \
  docker compose --profile observability up -d                 # + prometheus, grafana, jaeger (izleme açık)
docker compose --profile loadtest run --rm k6                  # yük testi (docs/yuk-testi.md)
```

Not: ilk derlemede api/worker/migrate aynı imajı paralel ürettiği için nadiren
`image ... already exists` hatası görülebilir; komutu tekrar çalıştırmak yeterlidir (ikinci derleme önbellekten).

### Gözlemlenebilirlik
- **Prometheus** (`infra/prometheus/prometheus.yml`): `api` işi Docker DNS servis keşfi (`dns_sd_configs`,
  `names: [api]`, A kaydı, :8000) ile her kopyayı ayrı hedef olarak kazır; `--scale` sonrası hedefler kendiliğinden
  güncellenir. `worker:9101/metrics` (LLM metrikleri worker sürecinde oluşur). `METRICS_TOKEN` tanımlıysa
  api işine `authorization` eklenmelidir.
- **Grafana** (`infra/grafana/`): Prometheus + Jaeger veri kaynakları ve "İlaç Uyumu Koçu — Genel" panosu
  hazır gelir: istek/sn (rota ve **kopya bazında** — yük dengelemeyi gösterir), p95 gecikme (rota bazında),
  5xx/429/4xx oranı, LLM token/sn, LLM maliyeti (USD), LLM p95 süresi, guardrail eylemleri, devre kesici
  durumu, koç DLQ boyutu, ayaktaki api kopyası sayısı.
- **Jaeger** (OpenTelemetry): `backend/app/observability.py setup_tracing()` yalnızca
  `OTEL_EXPORTER_OTLP_ENDPOINT` tanımlıysa FastAPI + SQLAlchemy + httpx + redis enstrümantasyonunu açar
  (OTLP/HTTP → `jaeger:4318`). Servis adları `ilac-kocu-api` / `ilac-kocu-worker`, `service.instance.id` =
  konteyner adı (hangi kopya). Gizlilik: gövde ve başlıklar span'e yazılmaz; URL sorgu dizeleri silinir; SQL
  parametresiz, Redis komutları argümansız (`GET ?`) kaydedilir; `/health*` ve `/metrics` izlenmez.

## TLS (isteğe bağlı, varsayılan kapalı)

```sh
sh infra/nginx/gen-cert.sh                                     # infra/nginx/certs/server.{crt,key} (self-signed, git dışı)
docker compose -f docker-compose.yml -f infra/nginx/compose.tls.yml up -d
curl -k -I https://localhost/giris                             # Strict-Transport-Security başlığı görünür
```

`compose.tls.yml` 443'ü yayınlar, `infra/nginx/tls.conf`'u ve sertifikaları bağlar. HSTS yerel sertifikada
`max-age=300` (localhost uzun süre HTTPS'e kilitlenmesin); üretimde `max-age=31536000; includeSubDomains`,
gerçek sertifika ve :80'de `return 301 https://$host$request_uri;` kullanın.

## Dosyalar

| Yol | İçerik |
|---|---|
| `infra/nginx/nginx.conf` | http düzeyi: resolver, upstream'ler, log biçimi, gzip, sınırlar |
| `infra/nginx/conf.d/default.conf` | :80 dış sunucu ve :8081 iç API ağ geçidi |
| `infra/nginx/snippets/` | güvenlik başlıkları, web proxy ayarları, ortak konumlar |
| `infra/nginx/templates/` | açılışta ortam değişkeniyle üretilen istek sınırı bölgesi |
| `infra/nginx/tls.conf`, `compose.tls.yml`, `gen-cert.sh` | isteğe bağlı TLS |
| `infra/prometheus/prometheus.yml` | kazıma hedefleri |
| `infra/grafana/provisioning`, `infra/grafana/dashboards` | veri kaynakları ve pano |
| `infra/k6/` | yük testi senaryosu, sonuçlar `infra/k6/results/` (git dışı) |
