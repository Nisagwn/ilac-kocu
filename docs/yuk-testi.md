# Yük testi: 1 ve 3 API kopyası, kopya durdurma, istek sınırı

Betikler: `infra/k6/` (k6 0.55). Yığın: `docker compose` (nginx 1.28 → API kopyaları, PostgreSQL 16 + pgvector, Redis 7, işçi). Ölçüm tarihi: 2026-10-17.

## Ortam ve sınırlılıklar

- Tek makine: **4 vCPU, 15 GB RAM**; k6, nginx, PostgreSQL ve tüm API kopyaları aynı makinede. Sayılar bu makineye özgüdür, mutlak kapasite değil, **göreli karşılaştırma** içindir.
- Her API kopyası tek uvicorn süreci (tek Python yorumlayıcısı). Kapasite ölçümünde istek sınırları gevşetildi (`.env`: `API_RATE_LIMIT_ENABLED=false`; `NGINX_RATE_LIMIT=10000r/s`, `NGINX_RATE_BURST=10000`), izleme kapalıydı (`OTEL_EXPORTER_OTLP_ENDPOINT` boş). İstek sınırı testi varsayılan ayarlarla yapıldı.
- Sentetik demo verisi (`python -m app.seed`); test kullanıcıları `hasta@demo.local` ve `hekim@demo.local`. LLM kapalı (`COACH_PROVIDER=rule`): koç uçları bu teste dahil değil.

## 1 kopya ve 3 kopya (`load.js`)

Senaryo: 30 eşzamanlı sanal kullanıcı, istekler arası 50 ms, 10 sn artış + 60 sn sabit + 5 sn iniş. Karışım: %80 hasta okuması (bugünün dozları, son 30 gün ölçüm — sayfalı `limit=100`, uyum, ilaçlar, `/auth/me`), %10 hekim hasta listesi, %10 ölçüm yazma (`Idempotency-Key` ile). Her koşu aynı veriyle başladı (test ölçümleri `note='k6'` ile işaretlenip koşu sonunda silindi). Her yapılandırma iki kez koşturuldu.

| Kopya | Koşu | İstek/sn | p95 | Hata | CPU (koşu ortası) |
|---|---|---|---|---|---|
| 1 | a | 67,1 | 947 ms | %0 | api 115 %, db 24 % |
| 1 | b | 67,9 | 941 ms | %0 | api 137 %, db 24 % |
| 3 | a | 192,3 | 296 ms | %0 | api 83 / 82 / 79 %, db 73 % |
| 3 | b | 193,3 | 297 ms | %0 | api 84 / 84 / 81 %, db 74 % |

- **3 kopya: verim 2,9 kat, p95 üçte bir.** Tek kopyada darboğaz API sürecidir (tek Python süreci ~1 çekirdeği doyurur, PostgreSQL %24'te boşta bekler). 3 kopyada yük üçe neredeyse eşit bölünür (nginx `least_conn`; 3b'de kopya başına ~%33) ve darboğaz makinenin toplam CPU'suna kayar (~%330 / %400).
- Yazmaların p95'i (3 kopya, 3b): 210 ms; okumaların 309 ms. 14 657 kontrolün tamamı geçti.
- Bu makinede 4. kopya anlamlı kazanç getirmez (çekirdekler dolu); gerçek ortamda kopyalar ayrı makinelere/çekirdeklere dağıtılmalı.

**Yöntem notu (ilk ölçüm hatası):** İlk denemede ölçüm okuması sayfasızdı (`days=30`, tümü) ve test her koşuda ~1 400 ölçüm yazıyordu; yanıtlar koşudan koşuya büyüdüğü için verim 158 → 124 → 92 istek/sn diye düştü ve "3 kopya 1 kopyadan yavaş" gibi yanlış bir sonuç çıktı. Düzeltme: okuma sayfalı yapıldı, test yazmaları işaretlenip her koşudan sonra silindi, veritabanı sıfırdan seed edildi. Yukarıdaki tablo bu düzeltmeden sonradır.

## Kopya durdurma (`failover.js`)

3 kopya, hekim hasta listesi (`GET /v1/patients`) **15 istek/sn sabit hızda** 60 sn; 22. saniyede bir kopya durduruldu. Dağılım nginx erişim logundan (son yanıt veren kopya).

| Durdurma | İstek | Hata | p95 | Durdurulan kopyanın payı | Başka kopyaya aktarılan |
|---|---|---|---|---|---|
| `docker stop` (SIGTERM, düzgün kapanma) | 902 | **0** | 83 ms | 94 (yalnızca durdurulana kadar) | 3 |
| `docker kill` (SIGKILL, ani ölüm) | 902 | 1 (%0,11, 502) | 82 ms | 92 | 3 |

- Düzgün kapanmada uvicorn yeni bağlantı almaz, süren istekleri bitirir; o sırada gelen birkaç istek nginx tarafından diğer kopyaya aktarıldı ve hiçbiri kullanıcıya hata olarak dönmedi.
- Ani ölümde 1 istek 502 aldı: ölen kopyanın IP'si DNS yenilenene kadar (≤10 sn) listede kalır ve o istek iki denemesini de tüketti. Bulgu üzerine `proxy_next_upstream_tries` 2 → 3 yapıldı (infra/nginx/snippets/proxy.conf). Yazma istekleri (POST) nginx tarafından yeniden gönderilmez; istemci `Idempotency-Key` ile güvenle tekrarlar.
- **Ek bulgu, hekim hasta listesinin maliyeti:** aynı test 40 istek/sn'de koşturulunca sistem ancak ~33 istek/sn verebildi, p95 2,2 sn oldu (kopya durdurmadan bağımsız). `GET /v1/patients` hasta başına uyum skorunu birkaç kez hesaplar (7 gün, önceki 7 gün eğilimi, 30 gün; her biri ayrı sorgu) ve 3 kopyanın ~%250 CPU'suyla istek başına ~75 ms CPU harcar. Hekim başına birkaç hasta ve insan hızında gezinme için yeterli; büyük listeler için uyum özetinin önbelleğe alınması ya da toplu (tek sorguda) hesaplanması gerekir. **Açık iş.**

## İstek sınırı (`ratelimit.js`, varsayılan ayarlar)

| Katman | Test | Sonuç |
|---|---|---|
| nginx IP sınırı (20 r/sn + 40 patlama) | Tek IP'den 100 eşzamanlı istek | 57'si 429; JSON gövde + `Retry-After: 1` |
| API token bucket (kullanıcı başına 120 jeton, 2/sn) | nginx sınırının altında, 15 r/sn | Kova 104. istekte boşaldı (patlamada harcanan jetonlardan sonra); 429 + `Retry-After` + `X-RateLimit-Remaining: 0` |
| Anonim patlama (curl, 100 istek / 50 paralel) | | 54 × 429 (nginx), 46 × 401 (API) |

Tüm 222 kontrol geçti. **Bulgu:** ilk denemede nginx'in 429'u HTML gövdeli ve `Retry-After`'sızdı; istemciler için API'nin 429'uyla tutarsızdı. Düzeltme: `error_page 429` ile JSON gövde ve `Retry-After` (infra/nginx/templates/default.conf.template).

## Yeniden üretme

```bash
cp .env.example .env && docker compose up -d --build && docker compose exec api python -m app.seed
# kapasite ölçümü için sınırları gevşet (.env: API_RATE_LIMIT_ENABLED=false) ve:
export NGINX_RATE_LIMIT=10000r/s NGINX_RATE_BURST=10000
API_REPLICAS=1 docker compose up -d --scale api=1
docker compose --profile loadtest run --rm -e VUS=30 -e DURATION=60s -e THINK=0.05 k6 run /scripts/load.js
docker compose exec db psql -U ilac -d ilac_kocu -c "DELETE FROM measurements WHERE note='k6'"
API_REPLICAS=3 docker compose up -d --scale api=3     # ve aynı komut
# kopya durdurma: bir terminalde failover.js (RATE=15), ~20. saniyede diğerinde:
docker stop $(docker compose ps -q api | head -1)     # ya da docker kill
# istek sınırı: .env'den API_RATE_LIMIT_ENABLED=false'u kaldır, NGINX_* değişkenlerini unset et, yığını yeniden başlat
docker compose --profile loadtest run --rm k6 run /scripts/ratelimit.js
```

Kopya sayısı yalnızca `--scale` ile değiştirilmeli; `API_REPLICAS` tek başına çalışan yığını küçültmez (bu ölçümde doğrulandı: her koşudan önce `docker compose ps api` ile kopya sayısı kontrol edildi).
