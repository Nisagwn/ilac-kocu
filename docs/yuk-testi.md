# Yük testi (k6)

Senaryo: `infra/k6/sabah-yogunlugu.js` — 08:00 ilaç saatinde hastaların uygulamayı aynı anda açması.

| Sanal kullanıcı | Akış |
|---|---|
| hasta (`ramping-vus`: 30 sn'de 0 → `PEAK_VUS`, `DURATION` boyunca sabit, 15 sn'de 0) | giriş → `GET /v1/patients/{id}/doses/today` → `PUT .../doses` ("aldım", `Idempotency-Key`) → `POST .../measurements` (açlık kan şekeri) → `GET .../measurements?days=30` |
| hekim (`constant-vus`: `CLINICIANS`) | giriş → `GET /v1/patients` (uyum skorlu hasta listesi) → `GET /v1/alerts`, 5 sn'de bir |

Koç/LLM uçları senaryoda **yoktur**: gecikmeleri sağlayıcıya bağlıdır, `llm_request_duration_seconds` ile ayrı izlenir.

**Eşikler:** `http_req_duration{kind:api}` p95 < 300 ms (LLM ve giriş hariç tüm API istekleri),
`http_req_failed` < %1, `checks` > %99, giriş (bcrypt) p95 < 2 sn (ayrı izlenir; bcrypt kasıtlı olarak yavaştır).

Yalnızca sentetik hesaplar kullanılır (şifre `demo12345`).

## Hazırlık

```sh
# 1) Yığın — tüm sanal kullanıcılar tek IP'den (k6 konteyneri) gelir: giriş sınırlarını yükseltin
LOGIN_IP_LIMIT=1000 LOGIN_EMAIL_LIMIT=1000 docker compose up -d --build
# 2) Sentetik yük testi hesapları (app/seed.py'ye ve demo verisine dokunmaz; idempotent)
docker compose exec api python -m scripts.seed_load_test --patients 50 --clinicians 5
#    yuk-hasta-001..050@demo.local, yuk-hekim-01..05@demo.local; silmek için: --purge
# (isteğe bağlı) panolar: docker compose --profile observability up -d → http://localhost:3001
```

`PATIENTS=0` ile demo hesapları (`hasta@demo.local` / `hekim@demo.local`) kullanılır; e-posta başına giriş
sınırı nedeniyle yalnızca birkaç sanal kullanıcılık duman testi içindir.

## Çalıştırma

```sh
docker compose --profile loadtest run --rm k6
# parametreler (compose değişkeni → k6 ortamı):
K6_PEAK_VUS=100 K6_DURATION=2m K6_THINK=0.3 K6_LABEL=3kopya docker compose --profile loadtest run --rm k6
```

| Değişken | Varsayılan | Anlam |
|---|---|---|
| `K6_TARGET` | `gateway` | `gateway`: nginx:8081 → api×N (Bearer token). `web`: nginx:80 → Next.js proxy → api (çerez); bu modda `NGINX_RATE_LIMIT` yükseltilmeli (tek IP, 20 r/s) |
| `K6_PEAK_VUS` | 50 | en yüksek sanal hasta sayısı |
| `K6_PATIENTS` / `K6_CLINICIANS` | 50 / 5 | kullanılacak sentetik hesap sayısı (VU'lar hesaplara sırayla dağılır) |
| `K6_DURATION` | 2m | tepe yük süresi |
| `K6_THINK` | 1 | düşünme süresi çarpanı. Uygulamanın kullanıcı başına istek sınırı (yazma 60/dk) 0.3'ün altında 429 üretir — bu doğru davranıştır ama kapasite ölçümünü bozar |
| `K6_LABEL` | run | sonuç dosyası: `infra/k6/results/<LABEL>-summary.json` (git dışı) |

Windows Git Bash'te komut satırından betik yolu verilecekse `MSYS_NO_PATHCONV=1` gerekir.

Çıktı sonunda adım bazında ort/p95/p99/maks tablosu ve eşiklerin GEÇTİ/KALDI durumu yazılır; eşik aşılırsa
k6 sıfırdan farklı kodla çıkar (CI'da kullanılabilir).

## 1 vs 3 API kopyası karşılaştırması

```sh
docker compose up -d --scale api=1 --no-recreate        # nginx `resolve` sayesinde yeniden yükleme gerekmez
for i in 1 2 3; do curl -s -o /dev/null -D - http://localhost:8000/health/ready | grep -i x-upstream; done  # tek IP
K6_THINK=0.3 K6_PEAK_VUS=100 K6_LABEL=1kopya docker compose --profile loadtest run --rm k6

docker compose up -d --scale api=3 --no-recreate        # üç farklı X-Upstream görülmeli
K6_THINK=0.3 K6_PEAK_VUS=100 K6_LABEL=3kopya docker compose --profile loadtest run --rm k6
```

Aynı veri kümesiyle, aynı parametrelerle ve ardışık koşun (ölçüm tablosu her koşuda büyür; büyük farklar için
`--purge` + yeniden seed). Grafana'daki "İstek/sn — API kopyası bazında" paneli dağılımı, "p95 gecikme — rota
bazında" paneli farkı gösterir.

## Dayanıklılık: bir kopyayı durdurma

```sh
K6_DURATION=3m K6_THINK=0.3 K6_LABEL=kopya-durdurma docker compose --profile loadtest run --rm k6 &
sleep 60
docker stop $(docker compose ps -q api | head -1)        # SIGTERM → uvicorn süren istekleri bitirir (≤20 sn)
sleep 60
docker compose up -d --scale api=3 --no-recreate        # kopya geri döner, DNS ile dağıtıma girer
```

Beklenen: hata oranı < %1 (kapanan kopyaya yeni bağlantı açılamaz → nginx `proxy_next_upstream error` ile
isteği diğer kopyaya aktarır; `max_fails=3` sonrası kopya 10 sn dağıtımdan çıkar, Docker DNS kaydı ≤5 sn'de düşer).
Kopya kapanırken üzerinde süren idempotent olmayan bir istek (POST) tekrarlanmaz; istemci hata alabilir —
frontend'in `Idempotency-Key` ile güvenle yeniden deneyebilmesi bu yüzden önemlidir.

## Sonuç tablosu şablonu

| Tarih | Ortam (CPU/RAM) | api kopyası | Hedef | Tepe VU | THINK | istek/sn | API p95 (ms) | p99 (ms) | giriş p95 (ms) | hata % | 429 % | Eşikler |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | |

## Örnek sonuçlar (2026-10-18, yerel)

Ortam: Windows 11, Docker Desktop (12 vCPU, 7.6 GB), tüm yığın + k6 aynı makinede; hedef `gateway`,
50 sentetik hasta + 5 hekim hesabı, 30 sn artış + 60 sn tepe + 15 sn iniş.

| api kopyası | Tepe VU | THINK | istek/sn | API p95 (ms) | giriş p95 (ms) | hata % | 429 % | Eşikler |
|---|---|---|---|---|---|---|---|---|
| 3 | 50 | 1 | 19.1 | 37.6 | 450 | 0.00 | 0.00 | hepsi GEÇTİ |
| 3 | 60 | 0.1 | 149.6 | 295.3 | 403 | 5.92 | 5.94 | hata/check KALDI (kullanıcı başına istek sınırı) |
| 3 | 100 | 0.3 | 76.9 | 887.6 | 1402 | 0.00 | 0.00 | p95 KALDI |
| 1 | 100 | 0.3 | 69.9 | 1080.0 | 1437 | 0.03 | 0.00 | p95 KALDI |
| 3 (tekrar) | 100 | 0.3 | 86.4 | 1050.3 | 1561 | 0.00 | 0.00 | p95 KALDI |
| 3 → 2 (60. sn'de `docker stop api-2`) | 50 | 1 | 20.6 | 51.8 | 392 | 0.00 | 0.00 | hepsi GEÇTİ |

Kopya durdurma koşusunda nginx loglarında hiç 5xx yok; durdurulan kopya `Waiting for application shutdown`
→ `Application shutdown complete` ile düzgün kapandı, sonraki istekler kalan iki kopyaya dağıldı
(`X-Upstream` örneklemesi: 4 × 172.26.0.3, 8 × 172.26.0.7).

Adım bazında (3 kopya, 50 VU, THINK=1): bugünün dozları p95 17.6 ms, doz kaydı 37.9 ms, ölçüm ekleme 32.8 ms,
ölçüm listesi 16.5 ms, hekim hasta listesi 114.7 ms (p99 301 ms), uyarılar 17.1 ms.

Yorum:
- Gerçekçi düşünme süresiyle (THINK=1) 50 eşzamanlı hasta + 5 hekim eşiklerin çok altında kalıyor.
- THINK=0.1'de 429'lar uygulamanın **kullanıcı başına** istek sınırından (yazma 60/dk) gelir; tek bir kullanıcının
  dakikada 60'tan fazla yazması gerçekçi değildir, sınır doğru çalışıyor.
- 100 VU / THINK=0.3'te darboğaz API konteynerlerinin CPU'su (koşu sırasında her kopya %100-200 CPU, PostgreSQL
  ~%30). Sabah senaryosunda tüm VU'lar ilk 30 sn içinde giriş yaptığından bcrypt (kasıtlı yavaş) CPU'yu
  doldurur ve aynı anda gelen diğer isteklerin gecikmesini artırır. Aynı makinede yük üreteci, veritabanı ve
  üç kopya CPU paylaştığı için 1 → 3 kopya kazancı burada sınırlı görünür; kopyaların ayrı çekirdek/makinelerde
  olduğu bir ortamda ölçülmelidir. Ayrıca bcrypt iş parçacığında GIL'i bıraktığı için tek uvicorn süreci de
  birden çok çekirdek kullanabilir.
- Hekim hasta listesi en pahalı uç (her hasta için uyum skoru + uyarı sayıları ayrı sorgular, N+1). Hasta
  sayısı arttıkça ilk optimize edilecek yer burasıdır.
