# Altyapı

`docker-compose.yml` bu dizindeki yapılandırmaları kullanır. Yük testi sonuçları: [`docs/yuk-testi.md`](../docs/yuk-testi.md).

```
tarayıcı ──▶ web (Next.js :3000) ──▶ nginx (:8080, host 127.0.0.1:8000) ──▶ api × API_REPLICAS (:8000)
                                                                              │
                                                       PostgreSQL ◀──────────┼──────────▶ Redis
                                                                              │
                                                     worker × N (koç kuyruğu; zamanlanmış işler yalnızca liderde)
gözlemlenebilirlik (profil): Prometheus ◀─ /metrics (api kopyaları, worker:9101) ─▶ Grafana ; OTLP ─▶ Jaeger
```

## Çalıştırma

```bash
docker compose up -d --build                         # db, redis, migrate, api×3, nginx, worker, web
docker compose exec api python -m app.seed           # demo verisi
API_REPLICAS=1 docker compose up -d --scale api=1    # kopya sayısını değiştir (yük testi karşılaştırması)
docker compose up -d --scale worker=2                # ikinci işçi (yalnızca biri lider olur)
docker compose --profile observability up -d         # + Prometheus, Grafana, Jaeger
docker compose --profile loadtest run --rm k6 run /scripts/load.js
```

| Adres (yalnızca 127.0.0.1) | Ne |
|---|---|
| http://localhost:3000 | Web uygulaması |
| http://localhost:8000/docs | Swagger (nginx üzerinden; eski API portu korunur) |
| http://localhost:8000/health/ready | Hazırlık: DB, Redis, şema |
| http://localhost:9090 | Prometheus (profil `observability`) |
| http://localhost:3001 | Grafana, anonim görüntüleyici; "İlaç Koçu — API ve koç" panosu (profil `observability`) |
| http://localhost:16686 | Jaeger izleri (profil `observability`; `.env`'de `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`) |

## nginx (`nginx/`)

- **Yük dengeleme:** `least_conn`, `server api:8000 resolve`: Docker DNS'i "api" adını her kopyanın IP'sine çözer, kopya eklenip çıkınca 10 sn içinde güncellenir (nginx ≥ 1.27.3). Pasif sağlık kontrolü: 10 sn'de 3 hata → kopya 10 sn devre dışı.
- **Hata aktarımı:** bağlantı hatası/zaman aşımı/502/503'te istek diğer kopyaya aktarılır (en çok 3 deneme). nginx POST/PATCH'i varsayılan olarak yeniden göndermez (çift kayıt olmaz); istemci `Idempotency-Key` ile güvenle yeniden deneyebilir.
- **`X-Upstream`** yanıt başlığı yanıtı veren kopyanın adresini gösterir (yük dağılımı gözlemi, yük testi). Üretimde istenirse kaldırılabilir.
- **Güvenlik başlıkları:** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cross-Origin-Resource-Policy`; JSON uçlarında `Content-Security-Policy: default-src 'none'` (Swagger/ReDoc hariç, CDN script'i yükler). `server_tokens off`. HSTS yalnızca TLS sonlandıran bir vekilin arkasında açılmalı (yorum satırı).
- **gzip:** 1 KB üzeri JSON/metin. Gzip'lenen yanıtta nginx API'nin ETag'ini zayıf (`W/`) yapar; API `If-None-Match` karşılaştırmasında `W/` önekini dikkate almaz, 304 çalışmaya devam eder.
- **İstek sınırı:** IP başına `NGINX_RATE_LIMIT` (20 r/s) + `NGINX_RATE_BURST` (40), aşımda 429. Kaba DoS koruması; kullanıcı başına ince sınır API'deki token bucket'tadır (`docs/M2/api-platform.md`).
- **Gerçek istemci IP'si:** `real_ip` yalnızca Docker iç ağından (web konteyneri) gelen `X-Forwarded-For`'a güvenir; API'ye `X-Forwarded-For: <gerçek IP>` iletilir ve API buna yalnızca `TRUSTED_PROXIES`'ten gelince güvenir. Dışarıdan sahte başlık gönderen istemci kendi adresiyle sınırlanır.
- **Log:** JSON, sorgu dizesi yazılmaz (ilaç adı vb. içerebilir); istek kimliği API loglarıyla aynıdır (`X-Request-ID`).
- `/metrics` dışarıya kapalı (404); Prometheus kopyalardan iç ağda doğrudan toplar.

## Düzgün kapanma ve sağlık kontrolleri

| Servis | Kapanma | Sağlık kontrolü |
|---|---|---|
| api | `uvicorn --timeout-graceful-shutdown 20`, `stop_grace_period: 30s`: yeni bağlantı alınmaz, süren istekler biter; nginx o sırada diğer kopyalara aktarır | `GET /health/live` (10 sn) |
| worker | SIGTERM: elindeki koç işi ve çalışan zamanlanmış iş bitirilir, liderlik kilidi bırakılır (`docs/M5/dayaniklilik.md`), `stop_grace_period: 30s` | — |
| nginx | — | `GET /nginx-health`; web, nginx sağlıklı olunca başlar |
| db, redis | — | `pg_isready`, `redis-cli ping` |

`/health/ready` (DB + Redis + alembic head) yük dengeleyiciye ekleme/çıkarma kararı içindir; konteyner sağlık kontrolü bilinçli olarak `/health/live` kullanır (veritabanı kısa süre kesilince tüm kopyalar yeniden başlatılmasın).

## Gözlemlenebilirlik (`prometheus/`, `grafana/`, Jaeger)

- Prometheus API kopyalarını DNS ile keşfeder (`dns_sd_configs: api`), kopya sayısı değişince hedefler kendiliğinden güncellenir; işçi metrikleri `worker:9101`.
- Grafana panosu (`grafana/dashboards/ilac-kocu.json`, kodla üretildi): istek hızı, p95, 5xx oranı, 429 hızı, ayakta kopya sayısı, DLQ, **kopya başına istek** (yük dağılımı), p50/p95/p99, durum sınıfları, en yavaş rotalar, LLM gecikmesi, devre kesici durumu, guardrail eylemleri, LLM maliyeti. Sağlık kontrolü istekleri trafikten hariç tutulur.
- İzleme (OpenTelemetry → Jaeger) `OTEL_EXPORTER_OTLP_ENDPOINT` ile açılır (`backend/app/tracing.py`). Span'lerde yol kimlikleri `{id}` olur, sorgu dizesi, istemci IP'si ve user-agent atılır; gövde/başlık/SQL parametresi yazılmaz. Elle doğrulandı: Jaeger'de `ilac-kocu-api` ve `ilac-kocu-worker` izleri, `http.target: /v1/patients/{id}/insights`.

## Yük testi (`k6/`)

| Betik | Amaç |
|---|---|
| `load.js` | Hasta + hekim okuma/yazma karışımı (`VUS`, `DURATION`, `WRITE_RATIO`, `THINK`); eşikler: hata < %1, okuma p95 < 500 ms |
| `failover.js` | Sabit hızda trafik sürerken bir API kopyası durdurulur; hata oranı ve kopya başına dağılım |
| `ratelimit.js` | Varsayılan sınırlarla: tek kullanıcı kovayı boşaltınca 429 + `Retry-After`, `X-RateLimit-*` başlıkları |

Testin yazdığı ölçümler `note = 'k6'` taşır; temizlik:
`docker compose exec db psql -U ilac -d ilac_kocu -c "DELETE FROM measurements WHERE note='k6'"`
