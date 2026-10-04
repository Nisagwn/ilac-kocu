# Kurulum Talimatı

Bu belge platformu **Windows, macOS ve Linux** üzerinde iki yolla çalıştırmayı anlatır:

- **A. Docker Compose (önerilen)** — tek komutla PostgreSQL + pgvector, Redis, API ve web arayüzü.
- **B. Yerel geliştirme** — backend ve frontend'i doğrudan makinenizde çalıştırma.

> Demo yalnızca sentetik veriyle çalışır. Gerçek hasta verisi girmeyin.

---

## 0. Gereksinimler

| Araç | Sürüm | Hangi yol | Not |
|---|---|---|---|
| Git | herhangi | A, B | |
| Docker Desktop (Windows/macOS) veya Docker Engine + Compose v2 (Linux) | Compose v2 (`docker compose`) | A | Windows'ta WSL 2 arka ucu önerilir |
| Node.js | 22 LTS | A (yalnızca VAPID anahtarı için), B | `npx` ile gelir |
| Python | 3.12 | B | |
| PostgreSQL 16 + pgvector | | B | En kolayı: yalnızca `db` servisini Docker'da çalıştırmak |

Kontrol:

```bash
git --version
docker compose version
node --version      # v22.x
python --version    # 3.12.x (Windows'ta `py --version` da olur)
```

---

## A. Docker Compose ile kurulum

### A1. Depoyu alın

```bash
git clone <depo-adresi> ilac-kocu
cd ilac-kocu
```

### A2. Ortam dosyasını oluşturun

**macOS / Linux / Git Bash:**

```bash
cp .env.example .env
```

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

### A3. `JWT_SECRET` üretin

En az 32 karakterlik rastgele bir değer üretip `.env` içindeki `JWT_SECRET=` satırına yazın.

```bash
# macOS / Linux / Git Bash
openssl rand -hex 32
# veya
python -c "import secrets; print(secrets.token_hex(32))"
```

```powershell
# Windows PowerShell
python -c "import secrets; print(secrets.token_hex(32))"
```

### A4. VAPID anahtarlarını üretin (web push için)

```bash
npx web-push generate-vapid-keys
```

Çıktıdaki **Public Key** ve **Private Key** değerlerini `.env` içine yazın:

```dotenv
VAPID_PUBLIC_KEY=BNc...(uzun base64url)
VAPID_PRIVATE_KEY=3Kx...
VAPID_SUBJECT=mailto:siz@ornek.com
```

Node yoksa Python alternatifi: `pip install py-vapid && vapid --gen && vapid --applicationServerKey`.

> Anahtarlar boş bırakılırsa uygulama yine çalışır; yalnızca push uç noktaları **503** döner ve hatırlatma zamanlayıcısı başlamaz. Zamanlayıcıyı tamamen kapatmak için `REMINDERS_ENABLED=false`.

### A5. (İsteğe bağlı) LLM koç anahtarı

Önerilen yol **OpenRouter** (tek anahtarla birçok model; https://openrouter.ai/keys):

```dotenv
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=anthropic/claude-sonnet-5.5     # boş bırakılırsa da bu kullanılır
OPENROUTER_DATA_COLLECTION=deny           # yalnızca istemleri saklamayan/eğitimde kullanmayan sağlayıcılar
```

| Model (OpenRouter kimliği) | Fiyat (USD / 1M token, girdi/çıktı) | Not |
|---|---|---|
| `anthropic/claude-sonnet-5.5` | 2 / 10 | **Önerilen**: Türkçe kalite, araç kullanımı ve güvenlik talimatlarına uyum |
| `anthropic/claude-haiku-4.5` | 1 / 5 | Yarı maliyet; kısa koç yanıtları için yeterli olabilir |
| `openai/gpt-5.4-mini` | 0.75 / 4.5 | Başka sağlayıcı; değiştirirseniz `python -m scripts.eval_coach --live` ile guardrail davranışını yeniden doğrulayın |

**Ücretsiz seçenek (kredi harcamaz):** `LLM_MODEL=qwen/qwen3.8-27b:free`. 2026-10-04'te ücretsiz araç destekli modeller koç hattında denendi:

| Model | `data_collection=deny` | Araç kullanımı | Sonuç |
|---|---|---|---|
| `qwen/qwen3.8-27b:free` | ✅ | ✅ uyum + eğitim araması, RAG kaynakları | Veriye dayalı ve kaynaklı yanıt; **gecikme 40–200 sn**, dakikalık istek sınırı (429) |
| `nvidia/nemotron-3-super-120b-a12b:free` | ❌ (yalnızca `allow`) | ❌ | Araç çağırmadan yanıtladı |
| `dots-studio/dots-3-note-preview:free` | ✅ | ❌ | Araç çağırmadı, Türkçe-İngilizce karışık |
| `nvidia/nemotron-3-ultra-550b-a55b:free`, `openrouter/free` | — | — | JSON şemasına uymadı |

Ücretsiz modelle önerilen ayarlar: `LLM_TIMEOUT_SECONDS=90`, `LLM_MAX_TOOL_ITERATIONS=3`. Adaptör 429 yanıtlarını `Retry-After` ile 2 kez yeniden dener; yine başarısız olursa koç kural tabanlı yanıta düşer. Ücretsiz uçların fiyatı bütçede 0 sayılır. Canlı demo için gecikme sorun olursa ücretli `anthropic/claude-haiku-4.5` önerilir.

Kabaca: bir koç isteği (en fazla 4 model çağrısı) ≈ 12 bin girdi + 1 bin çıktı token → Sonnet 5.5'te ≈ 3–4 sent. OpenRouter her yanıtta gerçek maliyeti döndürür ve bütçe (`llm_usage`) bu tutarla tutulur.

Doğrudan Anthropic API kullanmak için `OPENROUTER_API_KEY` boş bırakılıp `ANTHROPIC_API_KEY=sk-ant-...` girilir (`LLM_PROVIDER=auto` ikisinden hangisi doluysa onu seçer; ikisi de doluysa OpenRouter).

- Boş bırakılırsa koç **kural tabanlı yedek** moduna geçer: acil belirti → 112, doz sorusu reddi ve uyum verisine dayalı motivasyon mesajları yine çalışır; yalnızca serbest dilde LLM yanıtı ve LLM haftalık anlatısı olmaz.
- Maliyet sınırları (`.env`): `LLM_DAILY_PATIENT_REQUESTS=30`, `LLM_DAILY_PATIENT_TOKENS=300000`, `LLM_DAILY_SUMMARY_REQUESTS=3`, `LLM_MONTHLY_BUDGET_USD=50`. Sayaçlar veritabanındaki `llm_usage` tablosundadır; limit aşılırsa koç kural tabanlı yanıt verir. Model `LLM_MODEL` ile seçilir (yukarıdaki tablo).
- İsteğe bağlı embedding sağlayıcısı: `EMBEDDING_PROVIDER=voyage` ve `VOYAGE_API_KEY=...` (model `voyage-3.5-lite`, 384 boyuta kırpılır) (varsayılan: çevrimdışı yerel embedding, anahtar gerekmez).
- İsteğe bağlı `OPENFDA_API_KEY`: anahtarsız da çalışır, yalnızca günlük istek limiti düşüktür.

### A6. Başlatın

```bash
docker compose up --build
```

İlk derleme birkaç dakika sürebilir. `api` servisi açılırken migration'ları otomatik uygular (`alembic upgrade head`). Arka planda çalıştırmak için `-d` ekleyin.

### A7. Demo verisini yükleyin

Yeni bir terminalde:

```bash
docker compose exec api python -m app.seed
```

`Demo verisi oluşturuldu.` mesajını görmelisiniz (ikinci çalıştırmada `Demo verisi zaten var.` — güvenlidir).

Ek komut gerekmez: seed, 31 kürate Türkçe eğitim içeriğini otomatik olarak embed eder. Embedding sağlayıcısını sonradan değiştirirseniz (ör. `local` → `voyage`) vektörleri yeniden hesaplayın:

```bash
docker compose exec api python -m app.services.rag.reindex --all
```

openFDA etiketleri hazır yüklenmez, istek üzerine eklenir: ilaç bilgi kartı (`GET /drugs/label`) başarılı olursa içerik otomatik eğitim korpusuna girer; elle eklemek için Swagger'da (`Authorize` sonrası) `POST /education/ingest-drug?name=metformin`.

### A8. Adresler ve demo hesapları

| Adres | İçerik |
|---|---|
| http://localhost:3000 | Uygulama (PWA) |
| http://localhost:8000/docs | Swagger / OpenAPI |
| http://localhost:8000/health | Sağlık kontrolü (`{"status":"ok"}`) |

| Hesap | Şifre | Rol | Açılış sayfası |
|---|---|---|---|
| `hasta@demo.local` | `demo12345` | Hasta (T2DM + HT, 30 günlük geçmiş) | `/bugun` |
| `hekim@demo.local` | `demo12345` | Hekim (hastanın hekimi) | `/hekim` |
| `yakin@demo.local` | `demo12345` | Bakım veren (salt okunur) | `/yakin` |

> Rolleri aynı tarayıcıda değiştirirken önce çıkış yapın ya da her rol için ayrı profil / gizli pencere kullanın (oturum çerezi tarayıcı başınadır).

### A9. Durdurma ve sıfırlama

```bash
docker compose down          # konteynerleri durdur (veri korunur)
docker compose down -v       # veritabanı volume'ü dahil her şeyi sil (demo verisini sıfırlar)
```

---

## B. Yerel geliştirme (Docker'sız uygulama)

En pratik yöntem veritabanı ve Redis'i Docker'da, uygulamayı yerelde çalıştırmaktır:

```bash
docker compose up -d db redis
```

### B1. Backend

**macOS / Linux / Git Bash:**

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate            # Git Bash'te: source .venv/Scripts/activate
pip install -r requirements-dev.txt

export DATABASE_URL=postgresql+psycopg://ilac:ilac@localhost:5433/ilac_kocu
export JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")

alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

**Windows PowerShell:**

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

$env:DATABASE_URL = "postgresql+psycopg://ilac:ilac@localhost:5433/ilac_kocu"
$env:JWT_SECRET  = python -c "import secrets; print(secrets.token_hex(32))"

alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

> Backend, çalışma dizinindeki `.env` dosyasını da okur (`backend/.env`). Ortam değişkenlerini her seferinde yazmak yerine kök `.env` dosyasını `backend/` içine kopyalayıp `DATABASE_URL`'i `localhost` olarak düzenleyebilirsiniz.

### B2. Frontend

```bash
cd frontend
cp .env.example .env.local           # PowerShell: Copy-Item .env.example .env.local
```

`.env.local` içeriği:

```dotenv
API_URL=http://localhost:8000
```

`API_URL` yalnızca Next.js **sunucusu** tarafından kullanılır (`/api/proxy`); tarayıcıya açılmaz, bu yüzden `NEXT_PUBLIC_` öneki yoktur.

```bash
npm ci
npm run dev                          # http://localhost:3000
```

Geliştirme modunda service worker `/sw.js?dev=1` olarak kaydedilir ve statik dosyaları önbelleğe almaz.

---

## C. Testleri çalıştırma

### Backend

```bash
cd backend
# (sanal ortam etkin)
pytest -q                            # tüm testler — SQLite bellek içi DB, gerçek LLM çağrısı yok
pytest tests/test_guardrails.py -v   # yalnızca guardrail red-team seti
ruff check app tests                 # lint
```

Testler PostgreSQL veya API anahtarı gerektirmez. Migration'ları PostgreSQL üzerinde CI'daki gibi doğrulamak için:

```bash
alembic upgrade head && alembic downgrade base && alembic upgrade head
```

### Frontend

```bash
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

### CI

GitHub Actions (`.github/workflows/ci.yml`) her push ve PR'da aynı adımları koşar: backend (ruff → guardrail seti → testler → PostgreSQL migration döngüsü → seed), frontend (lint → tip denetimi → derleme), ardından iki Docker imajının derlenmesi.

---

## D. Sorun giderme

### Port çakışması (`port is already allocated`, `address already in use`)

5433 (PostgreSQL, yalnızca 127.0.0.1), 8000 (API) veya 3000 (web) başka bir süreç tarafından kullanılıyor olabilir.

```bash
# macOS / Linux
lsof -i :5432
```

```powershell
# Windows
netstat -ano | findstr :5432
Get-Process -Id <PID>
```

Çözüm: o süreci durdurun (ör. yerel PostgreSQL servisi) ya da `docker-compose.yml` içinde **sol taraftaki** host portunu değiştirin, ör. `"5433:5432"`. Host portunu değiştirirseniz yerel geliştirmedeki `DATABASE_URL`'i de güncelleyin.

### Giriş yapılıyor ama hemen `/giris`'e geri dönüyor (çerez kaydedilmiyor)

Oturum çerezi üretim derlemesinde (`docker compose`, `npm run build && npm start`) **`Secure`** bayrağıyla yazılır. Tarayıcılar `Secure` çerezi yalnızca **HTTPS** veya **`http://localhost`** üzerinde kabul eder.

- `http://localhost:3000` kullanın; `http://127.0.0.1:3000` çoğu tarayıcıda da çalışır.
- Telefondan `http://192.168.x.x:3000` gibi yerel ağ IP'siyle bağlanırsanız çerez reddedilir. Çözümler: geliştirme modunda (`npm run dev`, `Secure` kapalı) test etmek ya da bir HTTPS tüneli (ör. `cloudflared tunnel`, `ngrok`) kullanmak.

### Web push bildirimi gelmiyor

1. `.env` içinde VAPID anahtarları dolu mu? `GET http://localhost:8000/push/vapid-public-key` 503 dönüyorsa anahtar yoktur. Anahtar ekledikten sonra `docker compose up -d --build api` ile API'yi yeniden başlatın.
2. Tarayıcı bildirim izni "Engellendi" mi? Adres çubuğundaki site ayarlarından izin verin.
3. Hasta ekranındaki "Test bildirimi gönder" (`POST /push/test`) ile zamanlayıcıdan bağımsız deneyin.
4. Zamanlayıcı yalnızca saati **son 15 dakikada** gelmiş ve henüz işaretlenmemiş dozlar için, doz başına günde **bir kez** gönderir. Demo için bir ilaca birkaç dakika sonrasına saat ekleyin.
5. Saat dilimi `TIMEZONE=Europe/Istanbul` ile yorumlanır; konteyner saati değil bu ayar esas alınır.

### iOS / iPadOS'ta push

- Web push yalnızca **iOS 16.4+** ve uygulama **ana ekrana eklenmişse** çalışır (Safari → Paylaş → Ana Ekrana Ekle). Safari sekmesinde bildirim izni istenemez.
- Güvenli bağlam gerekir: iPhone'dan erişim için HTTPS tüneli kullanın; yerel ağ IP'si üzerinden HTTP çalışmaz.
- Android Chrome ve masaüstü tarayıcılarda ek adım gerekmez.

### PowerShell ve bash farkları

| İşlem | bash / zsh / Git Bash | Windows PowerShell 5.1 |
|---|---|---|
| Dosya kopyalama | `cp .env.example .env` | `Copy-Item .env.example .env` |
| Ortam değişkeni | `export DATABASE_URL=...` | `$env:DATABASE_URL = "..."` |
| Sanal ortamı etkinleştirme | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Komut zinciri | `a && b` | `a; if ($?) { b }` (PowerShell 7'de `&&` desteklenir) |
| Satır devamı | `\` | `` ` `` (ters tırnak) |

- `Activate.ps1` "running scripts is disabled" hatası verirse: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- Windows'ta `python` komutu Microsoft Store'a yönlendiriyorsa `py -3.12` kullanın.
- Git ile çekilen kabuk betiklerinde CRLF sorunu yaşarsanız: `git config --global core.autocrlf input`.

### Diğer

| Belirti | Neden / çözüm |
|---|---|
| `api` konteyneri `connection refused` ile çıkıyor | `db` henüz hazır değil; compose healthcheck bekler, yine olursa `docker compose restart api` |
| `extension "vector" does not exist` | Düz `postgres` imajı kullanılıyor; `pgvector/pgvector:pg16` imajını kullanın |
| Seed sonrası hekim listesi boş | `hekim@demo.local` ile girildiğinden emin olun; başka bir hekim hesabı açtıysanız hasta o hekime atanmamıştır (404 politikası gereği görünmez) |
| Koç her zaman aynı kalıp yanıtı veriyor | `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` boş, günlük limit doldu veya API hatası → kural tabanlı yedek mod (beklenen davranış) |
| openFDA kartı "bulunamadı" | Ticari isim eşleme tablosunda yoksa etken madde adıyla (ör. `metformin`) deneyin |
| Değişiklikler görünmüyor (PWA) | Service worker eski kabuğu tutuyor olabilir: DevTools → Application → Service Workers → Unregister, sayfayı yenileyin |

---

## D2. Worker'ı Docker olmadan çalıştırma

- Redis varsa: `REDIS_URL=redis://localhost:6379/0` ayarlayın; bir terminalde `uvicorn app.main:app --reload`, diğerinde `python -m app.worker` (ikisi de `backend/` içinde).
- Redis yoksa: yalnızca uvicorn yeterli; koç işleri API sürecinde arka plan görevi olarak işlenir. Doz hatırlatmaları için `RUN_SCHEDULER_IN_API=true`.
- Migration'lar artık API açılışında çalışmaz; Docker'sız ortamda `alembic upgrade head` komutunu kendiniz çalıştırın.
- Zamanlayıcıyı yalnızca bir süreç çalıştırmalı; birden fazla worker başlatırsanız fazlalarında `REMINDERS_ENABLED=false` yapın.

## E. Gözlemlenebilirlik

API her isteğe bir **istek kimliği** atar: istemci `X-Request-ID` gönderirse (en fazla 128 karakter, harf/rakam/`.`/`_`/`-`) o kullanılır, yoksa üretilir; yanıtta `X-Request-ID` başlığıyla döner. Her istek için `app.access` adlı logger'a tek satır JSON yazılır:

```json
{"event": "http_request", "request_id": "3f2c...", "method": "GET", "route": "/patients/{patient_id}", "status": 200, "duration_ms": 12.4}
```

`route` ham yol değil **rota şablonudur**; sorgu değerleri, gövdeler ve kimlikler loglanmaz.

**Prometheus:** `GET /metrics` (OpenAPI'de görünmez). Ayarlar:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `METRICS_ENABLED` | `true` | `false` ise `/metrics` 404 döner |
| `METRICS_TOKEN` | boş | Tanımlıysa `Authorization: Bearer <token>` zorunlu (üretimde önerilir) |

| Metrik | Etiketler | Anlamı |
|---|---|---|
| `http_requests_total`, `http_request_duration_seconds` | method, route, status | İstek sayısı / süresi (eşleşmeyen yollar `route="unmatched"`) |
| `llm_request_duration_seconds` | purpose (coach/summary), model, outcome (ok/fallback/budget_exceeded/error) | LLM isteği süresi; `outcome="fallback"` oranı kural tabanlı koça düşme oranıdır |
| `llm_tokens_total` | purpose, model, kind (input/output/cache_read/cache_write) | Token kullanımı |
| `llm_cost_usd_total` | purpose, model | Maliyet (USD) |
| `llm_rate_limited_total` | — | OpenRouter'dan alınan 429 yanıtları (yeniden denemeler dahil) |
| `coach_guardrail_actions_total` | action | Kaydedilen koç yanıtlarındaki guardrail eylemi (`none`, `llm_fallback`, `budget_exceeded`...) |

Hiçbir etikette hasta/kullanıcı kimliği yoktur. Çoklu worker (`uvicorn --workers N`) ile her süreç kendi sayaçlarını tutar; birleşik görünüm için `PROMETHEUS_MULTIPROC_DIR` (prometheus-client çoklu süreç modu) ya da tek worker kullanın.

Örnek sorgular: fallback oranı `sum(rate(llm_request_duration_seconds_count{outcome="fallback"}[5m])) / sum(rate(llm_request_duration_seconds_count[5m]))`, p95 LLM gecikmesi `histogram_quantile(0.95, sum by (le) (rate(llm_request_duration_seconds_bucket[5m])))`.

**Paylaşımlı önbellek:** `REDIS_URL` tanımlıysa hekim özet anlatısı (`narr:<hasta>:<gün>:<dönem>:<parmak izi>`, 24 saat) ve openFDA etiket önbelleği (`openfda:label:<etken madde>`) tüm worker'lar arasında Redis'te paylaşılır; Redis yoksa süreç içi önbelleğe düşülür.

## F. Anlamsal embedding (isteğe bağlı)

Varsayılan `EMBEDDING_PROVIDER=local` çevrimdışı, deterministik bir hashing embedding'idir (sözcüksel; anlamsal değildir). Eşanlamlı ve farklı ifadeli soruları daha iyi eşleştirmek için yerel, çok dilli bir model kullanılabilir:

```bash
pip install -r requirements.txt -r requirements-ml.txt   # fastembed (ONNX, CPU; ~120 MB model ilk kullanımda indirilir)
```

`.env`:

```
EMBEDDING_PROVIDER=fastembed
# FASTEMBED_MODEL=intfloat/multilingual-e5-small   (varsayılan; 384 boyut — mevcut vector(384) sütunu, migration gerekmez)
# FASTEMBED_CACHE_DIR=/data/fastembed               (model dosyalarının kalıcı dizini)
```

Ardından içerikleri yeni modelle yeniden embed edin (her satır `embedding_model` alanını tuttuğu için sağlayıcı değişince eski vektörler zaten geçersiz sayılır; komut bunu önceden yapar):

```bash
python -m app.services.rag.reindex --all
```

e5 modelleri için sorgular `query: `, içerikler `passage: ` önekiyle otomatik embed edilir. `fastembed` yüklü değilse ya da model indirilemezse API çökmez: uyarı loglanır ve yerel embedding kullanılır.
