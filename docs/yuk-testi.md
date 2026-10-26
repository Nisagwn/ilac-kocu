# Yük testi (k6)

Senaryo: `infra/k6/sabah-yogunlugu.js` — 08:00 ilaç saatinde hastaların uygulamayı aynı anda açması.

| Sanal kullanıcı | Akış |
|---|---|
| hasta (`ramping-vus`: 30 sn'de 0 → `PEAK_VUS`, `DURATION` boyunca sabit, 15 sn'de 0) | (giriş bir kez, aşağıya bakın) → `GET /v1/patients/{id}/doses/today` → `PUT .../doses` ("aldım", `Idempotency-Key`) → `POST .../measurements` (açlık kan şekeri) → `GET .../measurements?days=30` |
| hekim (`constant-vus`: `CLINICIANS`) | (giriş bir kez) → `GET /v1/patients` (uyum skorlu hasta listesi) → `GET /v1/alerts`, 5 sn'de bir |

**Giriş bir kez yapılır.** `gateway` modunda her sentetik hesap `setup()` içinde bir kez giriş yapar (55 bcrypt
girişi, yaklaşık 14 sn, ölçüm penceresinin dışında); sanal kullanıcılar bu erişim token'ını kullanır. Token'ın
bitmesine 1 dk kala ya da 401 gelirse yalnız o VU yeniden giriş yapar. Yenileme token'ı kullanılmaz: aynı hesabı
paylaşan iki VU aynı yenileme token'ını döndürmeye çalışırsa ikinci kullanım "çalınmış token" sayılır ve hesabın
tüm oturumları iptal edilir. `web` modunda çerezler VU başına olduğundan her VU ilk iterasyonunda bir kez girer.
Özetteki "senaryo … istek/sn" satırı setup süresini ve setup isteklerini hariç tutar; tablolar bu değeri kullanır.

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
# Her api kopyası 1 CPU / 512 MB ile sınırlıdır (API_CPUS, API_MEMORY; aşağıdaki "Neden CPU sınırı?")
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
API_REPLICAS=1 docker compose up -d --wait nginx        # nginx `resolve` sayesinde yeniden yükleme gerekmez
for i in 1 2 3; do curl -s -o /dev/null -D - http://localhost:8000/health/ready | grep -i x-upstream; done  # tek IP
API_REPLICAS=1 K6_THINK=0.3 K6_PEAK_VUS=100 K6_DURATION=1m K6_LABEL=1kopya \
  docker compose --profile loadtest run --rm --no-deps k6

API_REPLICAS=3 docker compose up -d --wait nginx        # üç farklı X-Upstream görülmeli
API_REPLICAS=3 K6_THINK=0.3 K6_PEAK_VUS=100 K6_DURATION=1m K6_LABEL=3kopya \
  docker compose --profile loadtest run --rm --no-deps k6
```

`--scale` yerine `API_REPLICAS` kullanın: `compose run` bağımlılıkları başlatırken kopya sayısını compose
dosyasındaki değere geri çekebilir (`--no-deps` bunu da önler).

Kullanıcının çalışan yığınına dokunmadan ölçmek için ayrı proje adı ve portlar kullanılabilir:
`docker compose -p ilac-kocu-yuk` + `HTTP_PORT=8090 API_GATEWAY_PORT=8002 DB_PORT=5435
BACKEND_IMAGE=ilac-kocu-yuk-backend` (imaj adı ayrı olmalı, yoksa ana projenin `ilac-kocu-backend` imajının
üzerine yazılır); bitince `docker compose -p ilac-kocu-yuk down -v` yalnız o projenin hacmini siler.

### Neden CPU sınırı?

Tüm yığın tek makinede koşarken API kopyalarının kaynak sınırı yoksa tek kopya da makinenin bütün çekirdeklerini
kullanabilir (bcrypt GIL'i bırakır; Docker Desktop VM'i 12 vCPU), 3 kopya da aynı çekirdekleri paylaşır.
Böylece 1 → 3 kopya geçişi kapasite eklemez, yalnızca aynı CPU'yu üç sürece böler; önceki ölçümde farkın küçük
çıkmasının ana nedeni buydu. `deploy.resources.limits.cpus: ${API_CPUS:-1}` (ve `memory: ${API_MEMORY:-512M}`)
ile her kopya 1 çekirdekli ayrı bir sunucu gibi davranır; 3 kopya = 3 çekirdek. Bu, yatay ölçeklemenin
etkisini gösteren bir **modeldir**, gerçek çok sunuculu ortamın yerine geçmez (aşağıda sınırlılıklar).

Aynı veri kümesiyle, aynı parametrelerle ve ardışık koşun (ölçüm tablosu her koşuda büyür; büyük farklar için
`--purge` + yeniden seed). Grafana'daki "İstek/sn — API kopyası bazında" paneli dağılımı, "p95 gecikme — rota
bazında" paneli farkı gösterir.

## Dayanıklılık: bir kopyayı durdurma

```sh
K6_DURATION=3m K6_THINK=0.3 K6_LABEL=kopya-durdurma docker compose --profile loadtest run --rm k6 &
sleep 60
docker stop $(docker compose ps -q api | head -1)        # SIGTERM → uvicorn süren istekleri bitirir (≤20 sn)
sleep 60
docker start <durdurulan kopya>                          # kopya geri döner, DNS ile dağıtıma girer
```

Beklenen: hata oranı < %1 (kapanan kopyaya yeni bağlantı açılamaz → nginx `proxy_next_upstream error` ile
isteği diğer kopyaya aktarır; `max_fails=3` sonrası kopya 10 sn dağıtımdan çıkar, Docker DNS kaydı ≤5 sn'de düşer).
Kopya kapanırken üzerinde süren idempotent olmayan bir istek (POST) tekrarlanmaz; istemci hata alabilir —
frontend'in `Idempotency-Key` ile güvenle yeniden deneyebilmesi bu yüzden önemlidir.

## Sonuç tablosu şablonu

| Tarih | Ortam (CPU/RAM) | api kopyası | Hedef | Tepe VU | THINK | istek/sn | API p95 (ms) | p99 (ms) | giriş p95 (ms) | hata % | 429 % | Eşikler |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | |

## Yeniden ölçüm (2026-10-26): N+1 düzeltmesi + tek giriş + CPU sınırı

Ortam: aynı makine (Windows 11, Docker Desktop 12 vCPU / 7.6 GB), ayrı compose projesi (`-p ilac-kocu-yuk`),
temiz veritabanı + 50 sentetik hasta / 5 hekim, hedef `gateway`, api kopyası başına **1 CPU / 512 MB**.
Profil öncekiyle aynı: 30 sn artış + 60 sn tepe + 15 sn iniş, tepe 100 VU, THINK=0.3. İlk koşudan önce bir
ısınma koşusu yapıldı. Ölçüm sırasında aynı Docker'da başka projeler de (kullanıcının `ilac-kocu` ve
`learnup-agent` yığınları, boşta) çalışıyordu.

Yapılan değişiklikler:
1. **Hekim hasta listesi N+1:** uyum, eğilim, uyarı sayıları ve son ölçüm zaten toplu sorgulanıyordu; hasta
   adı (`patient.user.full_name`) ise hasta başına ayrı tembel yükleme sorgusuydu. `joinedload(Patient.user)`
   ile istek başına sorgu sayısı **2 hastada 9 → 7, 20 hastada 27 → 7** (`tests/test_patient_list_queries.py`,
   `before_cursor_execute` sayacı; kimlik doğrulama ve erişim kaydı dahil).
2. **k6 tek giriş:** bcrypt girişleri ölçüm penceresinden çıktı (önceden tüm VU'lar ilk 30 sn'de giriş yapıyor,
   bcrypt CPU'yu doldurup diğer isteklerin gecikmesini artırıyordu).
3. **CPU sınırı:** kopya başına 1 CPU.
4. **Ölçüm sırasında bulunan kilitlenme (giderildi):** CPU sınırıyla tek kopya ilk koşuda tamamen tıkandı
   (p95 57 sn, %5.65 hata, api CPU'su %5). Nedeni: senkron uçlar anyio iş parçacığı havuzunda (varsayılan 40)
   koşar, SQLAlchemy havuzu 5+10 bağlantıdır. Yük altında 40 iş parçacığının hepsi bağlantı beklerken, bağlantıyı
   tutan istekler sonraki adımları (uç fonksiyonu, `get_db` kapanışı) için iş parçacığı bulamıyor ve
   `pool_timeout` (30 sn) dolana kadar her şey duruyordu. Düzeltme: iş parçacığı havuzu 200
   (`THREADPOOL_TOKENS`), süreç başına eşzamanlı istek sınırı 150 (`UVICORN_LIMIT_CONCURRENCY` ←
   `API_LIMIT_CONCURRENCY`; aşılırsa 503). 150 < 200 + 15 olduğundan iş parçacıklarının tamamı bağlantı
   beklerken kalamaz. Önceki ölçümde CPU sınırı olmadığı için istekler hızlı bitiyor ve bu durum ortaya çıkmıyordu.

| Koşu | api kopyası | CPU sınırı | istek/sn | API p95 (ms) | hekim listesi p95 (ms) | giriş p95 (ms) | hata % | 429 % | Eşikler |
|---|---|---|---|---|---|---|---|---|---|
| **Önce** (2026-10-18) | 1 | yok | 69.9 | 1080.0 | – | 1437 | 0.03 | 0.00 | p95 KALDI |
| **Önce** (2026-10-18; iki koşu) | 3 | yok | 76.9 / 86.4 | 887.6 / 1050.3 | – | 1402 / 1561 | 0.00 | 0.00 | p95 KALDI |
| Ara: N+1 + tek giriş + CPU sınırı, kilitlenme düzeltmesi **öncesi** | 1 | 1 CPU | 11.0 | 57131.5 | 30070.8 | 268 | 5.65 | 0.00 | hata/check/p95 KALDI |
| **Sonra** | 1 | 1 CPU | 49.6 | 2569.3 | 1965.8 | 268 | 0.00 | 0.00 | p95 KALDI |
| **Sonra** | 3 | 1 CPU × 3 | 115.3 | 56.2 | 81.1 | 257 | 0.44 | 0.44 | hepsi GEÇTİ |

Önceki koşularda istek/sn toplam süreye bölünmüştü ve girişleri de içeriyordu; yeni değerler yalnız senaryo
penceresidir (setup girişleri hariç), bu yüzden istek/sn sütunları birebir karşılaştırılamaz; p95 ve hata
oranı karşılaştırılabilir. Önceki tabloda adım bazında hekim listesi yalnız 50 VU/THINK=1 için vardı:
p95 114.7 ms → aynı profilde şimdi 61.1 ms (aşağıdaki dayanıklılık koşusu, CPU sınırlı).

Koşu sırasında `docker stats` örneklemesi: 1 kopya api %102 CPU (sınırda, doymuş), PostgreSQL %9;
3 kopya her biri %57–65, PostgreSQL %38, k6 %15.

**Dayanıklılık (3 → 2 → 3):**

| Profil | Olay | istek/sn | API p95 (ms) | hata % | nginx 5xx | Eşikler |
|---|---|---|---|---|---|---|
| 50 VU, THINK=1, 2 dk tepe | 75. sn'de `docker stop api-2`, 60 sn sonra `docker start` | 20.3 | 33.5 | 0.00 | 0 | hepsi GEÇTİ |
| 100 VU, THINK=0.3, 1 dk tepe | 60. sn'de `docker stop api-2`, 45 sn sonra `docker start` | 82.7 | 926.1 | 0.00 | 0 | p95 KALDI |

İlk koşuda durdurma anında 4 istek nginx tarafından diğer kopyaya aktarıldı (`upstream` alanında iki adres),
durdurulan kopya `Waiting for application shutdown → Application shutdown complete` ile düzgün kapandı;
yeniden başlatılınca DNS ile dağıtıma geri girdi (son dakikada üç kopyaya 325 / 220 / 331 istek). Ağır profilde
iki kopya kalan 2 CPU ile yükü hatasız taşıdı ama gecikme eşiği aştı. Bu beklenen davranış: tepe yük için gereken
kapasite 3 kopyadır, bir kopya kaybında sistem yavaşlar ama hata vermez.

Yorum:
- CPU sınırıyla yatay ölçekleme net görünüyor: 1 → 3 kopyada istek/sn 2.3 kat, p95 2569 → 56 ms. 3 kopyada
  istek/sn artık sunucu değil yük profili tarafından sınırlanıyor (VU'lar düşünme süresi kadar bekliyor);
  tek kopya ise 1 CPU'da doyuyor.
- 3 kopyada %0.44 429: istekler hızlı yanıtlandığı için hesap başına (2 VU aynı hesabı paylaşır) yazma sınırı
  (60/dk) arada aşılıyor; uygulamanın kullanıcı başına sınırı doğru çalışıyor, kapasite sorunu değildir.
- Giriş p95 1.4–1.6 sn → 0.26 sn: girişler artık eşzamanlı API yüküyle yarışmıyor.

## Önceki sonuçlar (2026-10-18, yerel, CPU sınırı yok)

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
- Hekim hasta listesi en pahalı uçtu (o tarihte hasta adı hasta başına ayrı sorguyla yükleniyordu, N+1).
  2026-10-26'da giderildi (yukarıda).

## Sınırlılıklar

- **Aynı makine.** Yük üreteci (k6), nginx, PostgreSQL, Redis ve tüm api kopyaları aynı Docker Desktop VM'inde
  koşar. CPU sınırı kopyaları ayrı sunucu gibi davranmaya zorlar ama bellek bant genişliği, disk, ağ yığını
  ve diğer konteynerler (ör. ölçüm sırasında boşta çalışan başka projeler) paylaşılır. Gerçek çok sunuculu bir
  ortamda ağ gecikmesi eklenir; mutlak sayılar değişir, oranlar yol göstericidir.
- **Kısa koşular.** Docker Desktop'ın önceki uzun koşuda çökmesi nedeniyle koşular ~2–3 dk ile sınırlı tutuldu ve
  her yapılandırma bir kez ölçüldü; önceki ölçümde aynı yapılandırmanın tekrarları arasında ±%15 oynaklık vardı.
- **Test imajı.** PyPI erişimi ölçüm günü kararsız olduğu için api imajı sıfırdan derlenemedi; mevcut bağımlılık
  katmanının üzerine `HEAD`'deki backend kodu kopyalanarak üretildi (bağımlılık sürümleri 2026-10-18 imajındaki
  gibi).
- **Tek PostgreSQL.** Veritabanı sınırlanmadı ve ölçeklenmedi; 3 kopyada %38 CPU ile darboğaz değil, ancak daha
  fazla kopyada ilk darboğaz adayıdır (kopya başına en çok 15 bağlantı × N, `max_connections` 100).
