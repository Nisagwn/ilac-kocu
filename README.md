# İlaç Uyumu ve Yaşam Tarzı Koçu

[![CI](https://github.com/Nisagwn/ilac-kocu/actions/workflows/ci.yml/badge.svg)](https://github.com/Nisagwn/ilac-kocu/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Next.js 15](https://img.shields.io/badge/Next.js-15-black)
![PostgreSQL 16 + pgvector](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791)

Tip 2 diyabet ve hipertansiyon hastalarının ilaç ve ölçüm takibini yaptığı, LLM koçun **tıbbi tavsiye vermeden** kişiselleştirilmiş, veriye dayalı ve kaynaklı geri bildirim verdiği, hekim için uyum dashboard'u sunan sağlık platformu.

> ⚠️ Koç teşhis koymaz ve ilaç dozu önermez. Acil durumlarda **112**'yi arayın. Demo yalnızca sentetik veriyle çalışır.

**Öne çıkanlar**

- **Güvenlik LLM'e bırakılmaz:** acil belirti ve doz soruları LLM'den önce deterministik filtreyle yakalanır; yanıtlar LLM'den sonra ikinci bir doğrulayıcıdan geçer; red-team seti CI'da zorunludur.
- **Veriye dayalı koç:** Claude tool-use ile hastanın kendi uyum ve ölçüm verisini sorgular; uyum skorunu deterministik kod hesaplar, model yalnızca okur.
- **Kaynaklı yanıt:** RAG (pgvector) ile kürate Türkçe eğitim içeriği; modelin gösterdiği kaynaklar sunucuda doğrulanır.
- **Klinik triyaj:** hekim listesi kritik uyarı ve düşük uyuma göre sıralı; 30 günlük eğriler, doz takvimi, haftalık özet.
- **Gizlilik tasarımda:** rol bazlı erişim (yetkisiz → 404), httpOnly çerez, LLM'e kimlik gönderilmez, service worker sağlık verisini önbelleğe almaz.

## Durum

| Teslim | Tarih | Durum |
|---|---|---|
| M1 — Proje önerisi ve teknoloji seçimi | 23 Eki 2026 | ✅ `docs/M1/` |
| M2 — Veritabanı ve backend iskeleti | 13 Kas 2026 | ✅ `backend/` |
| M3 — Arayüz ve dashboard (MVP) | 11 Ara 2026 | ✅ `frontend/` (Next.js PWA), web push + hatırlatma zamanlayıcısı, 102 backend testi |
| M4 — Yapay zekâ bileşeni | 1 Oca 2027 | ✅ Claude tool-use koç, RAG (pgvector), maliyet limiti, 232 senaryoluk guardrail seti, 469 backend testi |
| M5 — Final teslim | 17 Oca 2027 | ✅ rapor, kurulum, demo senaryosu (`docs/M5/`) — video kayıt bekliyor |

## Ekranlar

| Rol | Sayfa | İçerik |
|---|---|---|
| Hasta (mobil) | `/bugun` | Günün dozları, tek dokunuşla "aldım / almadım", hatırlatma aç/kapat |
| | `/olcum` | Kan şekeri / tansiyon girişi, hedef bantlı 30 günlük grafik |
| | `/koc` | Koç sohbeti; acil yanıt kırmızı kart + "112'yi ara", kaynaklar yanıtın altında |
| | `/ilaclar` | İlaç planı, openFDA bilgi kartı |
| | `/ozet` | Haftalık özet |
| Hekim (masaüstü) | `/hekim` | Triyaj listesi: uyum bandı ve önceki haftaya göre eğilim oku, açık kritik uyarılar |
| | `/hekim/hasta/[id]` | Klinik özet kartı (uyum eğilimi, hedef aralıkta kalma, doz atlama nedenleri, ölçüm sıklığı), 30 günlük ölçüm eğrileri, doz takvimi, uyarılar, haftalık özet + anlatı |
| | `/hekim/uyarilar` | Açık uyarılar, onaylama |
| | `/hekim/egitim` | Eğitim içeriği onayı: onayla / reddet (not zorunlu) / geri al; reddedilen içerik koçun kaynaklarından çıkar |
| Bakım veren (salt okunur) | `/yakin`, `/yakin/hasta/[id]` | Bağlı hastanın doz, ölçüm ve özet görünümü |

**Hasta (mobil)**

<table>
<tr>
<td align="center"><img src="docs/gorseller/01-giris.png" width="180" alt="Giriş"><br><sub>Giriş</sub></td>
<td align="center"><img src="docs/gorseller/02-hasta-bugun.png" width="180" alt="Bugün"><br><sub>Bugün</sub></td>
<td align="center"><img src="docs/gorseller/03-hasta-olcum.png" width="180" alt="Ölçüm"><br><sub>Ölçüm</sub></td>
<td align="center"><img src="docs/gorseller/04-hasta-ilaclar.png" width="180" alt="İlaçlar"><br><sub>İlaçlar</sub></td>
<td align="center"><img src="docs/gorseller/05-hasta-ozet.png" width="180" alt="Özet"><br><sub>Özet</sub></td>
</tr>
</table>

**Koç: kaynaklı yanıt, doz sorusu reddi ve acil durumda 112 kartı**

<img src="docs/gorseller/06-hasta-koc.png" width="260" alt="Koç">

**Hekim (masaüstü)**

![Hekim hasta listesi](docs/gorseller/07-hekim-hasta-listesi.png)

![Hekim hasta detayı](docs/gorseller/08-hekim-hasta-detay.png)

![Hekim uyarılar](docs/gorseller/09-hekim-uyarilar.png)

**Bakım veren (salt okunur) ve API belgesi**

<img src="docs/gorseller/10-yakin-hastalar.png" width="260" alt="Bakım veren">

![Swagger](docs/gorseller/11-swagger.png)

Ayrıntı: [`frontend/README.md`](frontend/README.md)

## Hızlı başlangıç

```bash
cp .env.example .env                          # JWT_SECRET'ı değiştirin; VAPID ve OPENROUTER_API_KEY isteğe bağlı
docker compose up --build                     # PostgreSQL + pgvector, Redis, API, web — migration otomatik
docker compose exec api python -m app.seed    # sentetik demo verisi
```

| Adres | |
|---|---|
| http://localhost:3000 | Uygulama (PWA) |
| http://localhost:8000/docs | Swagger / OpenAPI |

Demo hesaplar (şifre `demo12345`): `hasta@demo.local`, `hekim@demo.local`, `yakin@demo.local`

LLM sağlayıcısı **OpenRouter** (önerilen model `anthropic/claude-sonnet-5.5`, bkz. [kurulum A5](docs/M5/kurulum.md)) ya da doğrudan Anthropic API olabilir; `OPENROUTER_API_KEY` ve `ANTHROPIC_API_KEY` boşsa koç kural tabanlı yedek modda çalışır (guardrail'ler aynen geçerlidir).
Windows / macOS / Linux adım adım kurulum, Docker'sız geliştirme ve sorun giderme: **[`docs/M5/kurulum.md`](docs/M5/kurulum.md)**

## Mimari

![Mimari şeması](docs/gorseller/12-mimari.png)

Uzun ve zamanlanmış işler ayrı bir **worker** serviste çalışır; API yalnızca istekleri karşılar. Redis kuyruk, istek sınırı, token iptali ve önbellek için ortak katmandır. Ayrıntı: [docs/M1/05-mimari.md](docs/M1/05-mimari.md).

```mermaid
flowchart LR
    subgraph C["Tarayıcı"]
        PWA["Next.js PWA<br/>hasta · hekim · bakım veren"]
        SW["Service Worker<br/>push · offline kabuk"]
    end
    PX["Next.js sunucusu<br/>/api/proxy · httpOnly çerez"]
    subgraph B["FastAPI"]
        RB["JWT + roller<br/>yetkisiz → 404"]
        GR["Guardrail<br/>girdi · çıktı"]
        CO["Koç<br/>Claude tool-use<br/>↘ kural tabanlı yedek"]
        AD["Uyum skoru<br/>deterministik"]
        RG["RAG"]
        SC["APScheduler<br/>+ pywebpush"]
    end
    DB[("PostgreSQL<br/>+ pgvector")]
    RD[("Redis")]
    LLM["Claude API"]
    FDA["openFDA"]
    WP["Push servisi"]

    PWA --> PX --> RB
    RB --> GR --> CO
    CO <--> LLM
    CO -->|tool| AD & RG
    AD & RG & RB --> DB
    RB --> FDA -.önbellek.-> RD
    SC --> WP --> SW
```

Bileşen ve koç isteği sıra diyagramı: [`docs/M1/05-mimari.md`](docs/M1/05-mimari.md) · Veri modeli: [`docs/er-diyagrami.md`](docs/er-diyagrami.md)

| Katman | Teknoloji |
|---|---|
| Frontend | Next.js 15 (App Router, PWA), TypeScript, Tailwind + shadcn/ui, Recharts |
| Backend | FastAPI, SQLAlchemy 2, Pydantic, Alembic, APScheduler, pywebpush |
| Yapay zekâ | Claude API (tool use + structured output), pgvector RAG, openFDA |
| Altyapı | PostgreSQL 16 + pgvector, Redis, Docker Compose, GitHub Actions |

## API özeti

Tüm uçlar `/v1` önekiyle yayınlanır (tabloda önek yazılmadı); sürümsüz eski yollar geriye dönük çalışır ve `Deprecation` başlığı taşır. Platform: token bucket istek sınırı (`X-RateLimit-*`, 429 + `Retry-After`), yazma isteklerinde `Idempotency-Key`, GET'te `ETag`/304, listelerde `limit` + `cursor` sayfalama, `/health/live` ve `/health/ready`. Ayrıntı: [docs/M2/api-platform.md](docs/M2/api-platform.md).

| Uç nokta | Açıklama | Rol |
|---|---|---|
| `POST /auth/register`, `/auth/login`, `GET /auth/me` | Kayıt (hasta için açık rıza zorunlu), JWT (15 dk) + yenileme token'ı; giriş 5/dk/IP | herkes |
| `POST /auth/refresh`, `POST /auth/logout` | Yenileme token'ı döndürme (tekrar kullanımda tüm oturumlar kapanır); çıkışta token iptali | herkes |
| `GET /patients/me/access-log` | Verilerime kimler baktı (KVKK erişim kaydı) | hasta |
| `GET /patients` | Hekim: hasta listesi (kritik uyarı + düşük uyum önce) | tümü |
| `GET /patients/{id}/adherence?days=7` | Uyum skoru (alınan / planlanan doz) | okuma |
| `GET /summaries/{id}` (eski: `/patients/{id}/summary`) | Haftalık özet | okuma |
| `/patients/{id}/medications` | İlaç + zamanlama CRUD (silme = pasife alma) | hasta yazar |
| `GET /patients/{id}/doses/today`, `PUT /patients/{id}/doses` | Bugün ekranı, "aldım/almadım" | hasta yazar |
| `/patients/{id}/measurements` | Kan şekeri / tansiyon; eşik dışı değer → uyarı | hasta yazar |
| `GET /alerts`, `POST /alerts/{id}/ack` | Hekimin açık uyarıları | hekim |
| `GET /drugs/search?q=gli` | İlaç kataloğu araması (TR ticari ad / etken madde); ilaç formunda otomatik tamamlama | tümü |
| `GET /drugs/label?name=Glifor` | openFDA etiketi (TR ticari isim → etken madde); başarılı sorgu eğitim içeriğine de eklenir (en iyi çaba; bakım veren hariç) | tümü |
| `GET /education` | Eğitim içeriği (RAG korpusu) listesi; `kind=curated\|openfda` süzgeci | giriş yapmış herkes |
| `GET /education/search?q=` | Anlamsal arama (kosinüs + anahtar kelime), kaynaklı sonuçlar | giriş yapmış herkes |
| `POST /education/ingest-drug?name=` | openFDA etiketinden eğitim içeriği üretir (doz bölümleri ayıklanır; hekim onayı bekler) | hasta, hekim |
| `POST /education/{id}/approve\|reject\|reset` | Eğitim içeriği hekim onayı; reddedilen içerik RAG'de kullanılmaz | hekim |
| `GET /patients/{id}/insights` | Klinik özet: uyum eğilimi, hedef aralıkta kalma, doz atlama nedenleri, ölçüm sıklığı | okuma |
| `POST /coach/jobs`, `GET /coach/jobs/{id}` | Koç: acil/doz mesajı anında (200), diğerleri kuyrukta (202 → sorgu); 10/dk | hasta |
| `POST /coach/chat`, `GET /coach/history` | Koç senkron (test/değerlendirme), sohbet geçmişi | hasta |
| `GET /metrics` | Prometheus metrikleri (isteğe bağlı `METRICS_TOKEN`); worker: `:9101/metrics` | operasyon |
| `GET /push/vapid-public-key`, `POST`/`DELETE /push/subscribe`, `POST /push/test` | Web push aboneliği; doz saatinde (15 dk pencere, günde bir kez) hatırlatma | hasta |

**VAPID anahtarı üretimi:** `npx web-push generate-vapid-keys` (veya Python: `pip install py-vapid && vapid --gen && vapid --applicationServerKey`). Çıkan anahtarları `.env` içindeki `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` alanlarına yazın; anahtarlar yoksa push uç noktaları 503 döner ve hatırlatma zamanlayıcısı başlamaz (`REMINDERS_ENABLED=false` ile tamamen kapatılabilir).

## Yapay zekâ koç

```
mesaj → [Katman 1: girdi filtresi] → [bütçe/limit] → Claude tool-use döngüsü → [kaynak doğrulama] → [Katman 3: çıktı doğrulayıcı] → yanıt
```

- **Tool'lar:** `get_adherence`, `get_measurements`, `get_medications`, `search_education`. Hasta kimliği hiçbir tool şemasında yer almaz; sunucu JWT'deki hastayı bağlar — model başka hastaya erişemez.
- **Structured output:** model `output_config.format` JSON şemasıyla `message`, `risk_level` (low/medium/high/emergency), `escalate_to_clinician`, `source_ids` döndürür; sunucu `source_ids`'i kaynak listesine (`sources`) çevirir. `escalate_to_clinician=true` hekime uyarı düşürür.
- **RAG:** 31 kürate Türkçe hasta eğitim içeriği ([`education.yaml`](backend/app/data/education.yaml); kaynaklar WHO, NHS, CDC, ADA, ESH, IWGDF, TEMD, Sağlık Bakanlığı, TİTCK — tümü `reviewed: false`, klinisyen gözden geçirmesi gerekir) + istek üzerine eklenen openFDA etiket özetleri. PostgreSQL'de pgvector `vector(384)` + HNSW (kosinüs); SQLite'ta (testler) JSON. Varsayılan embedding çevrimdışı ve deterministik (`EMBEDDING_PROVIDER=local`); isteğe bağlı Voyage (`voyage-3.5-lite`, 384'e kırpılır). Skor = 0,7 kosinüs + 0,3 anahtar kelime, eşik `RAG_MIN_SCORE=0.25`, `search_education` en iyi 3 sonucu döndürür.
- **Grounding:** `source_ids` yalnızca o istekte `search_education`'ın gerçekten döndürdüğü kimlikler (veya `0` = WHO raporu) ise kabul edilir; diğerleri sunucuda atılır.
- **Maliyet kontrolü:** hasta başına günde 30 istek ve 300 000 token (`LLM_DAILY_PATIENT_REQUESTS`, `LLM_DAILY_PATIENT_TOKENS`), günde 3 özet anlatısı (`LLM_DAILY_SUMMARY_REQUESTS`), aylık 50 USD toplam bütçe (`LLM_MONTHLY_BUDGET_USD`). Her çağrı `llm_usage` tablosuna yazılır (sayaçlar Redis'te değil veritabanındadır; mesaj içeriği tutulmaz). Limit aşılırsa kural tabanlı yanıt döner (`guardrail_action=budget_exceeded`).
- **Model ve döngü:** varsayılan `claude-opus-5-5` (`LLM_MODEL`; `claude-sonnet-5-5` daha ucuzdur), `effort=low`, `max_tokens=2048`, 30 sn zaman aşımı, istek başına en çok 4 model çağrısı (`LLM_MAX_TOOL_ITERATIONS`; son çağrıda `tool_choice=none`). Tool'lar `strict`; sistem istemi ve tool tanımları prompt cache'lenir; geçmişten son 6 mesaj gönderilir.
- **Yedek:** API anahtarı yok, limit doldu, API hatası/zaman aşımı/ret ya da geçersiz JSON → kural tabanlı koç (`guardrail_action=llm_fallback` veya `budget_exceeded`). Haftalık hekim anlatısı üretilemezse deterministik şablon.
- **Haftalık anlatı:** LLM, hekim için olgusal bir Türkçe anlatı yazar ("Dikkat:" bayraklarıyla); çıktı doğrulayıcıdan geçer, yasal uyarı eklenir, hasta/gün/veri parmak izi başına önbelleğe alınır. Yanıtta `narrative_source` ve `missed_by_time_of_day` alanları da bulunur.
- **Gizlilik:** LLM'e ad, e-posta veya kullanıcı kimliği gönderilmez; yalnızca mesaj ve sayısal özetler.

Ayrıntı: [final rapor §6](docs/M5/final-rapor.md#6-yapay-zekâ-bileşeni) · Uygulama: [`backend/app/services/llm/`](backend/app/services/llm/) (`coach_agent.py`, `patient_data.py`, `budget.py`, `pricing.py`, `narrative.py`) · Uyum skoru doğrulaması: [`docs/M4/uyum-skoru-dogrulama.md`](docs/M4/uyum-skoru-dogrulama.md)

## Guardrail'ler

Koçun güvenliği LLM'e bırakılmaz; üç katmanlıdır ([politika](docs/M1/02-guardrail-politikasi.md), [`guardrails.py`](backend/app/services/guardrails.py)):

1. **Girdi filtresi (LLM'den önce, deterministik):** kendine zarar / acil belirti → sabit 112 yanıtı + hekime kritik uyarı, **LLM çağrılmaz**; doz/ilaç değişikliği sorusu → sabit güvenli yanıt.
2. **LLM:** kısıtlı system prompt, tool-use, JSON şeması, kaynak doğrulama.
3. **Çıktı doğrulayıcı (deterministik):** doz önerisi, teşhis veya "ben doktorum" ifadesi içeren yanıt engellenir; olay içerik olmadan loglanır.

Ek kanal: ölçüm eşik denetimi (kan şekeri < 54 veya > 300 mg/dL, tansiyon ≥ 180/120 mmHg → kritik uyarı). Türkçe karakter ve büyük/küçük harf farkları normalize edilir ("GÖĞSÜM", "gogsum" aynı yakalanır). Acil yol API anahtarından ve bütçeden bağımsızdır.

**Red-team seti:** [`backend/tests/guardrail_scenarios.yaml`](backend/tests/guardrail_scenarios.yaml) — **232 senaryo**. Girdi katmanı: 61 acil belirti, 12 kendine zarar, 60 doz/ilaç değişikliği, 46 olağan sohbet (yanlış pozitif kontrolü); çıktı katmanı: 30 engellenmesi gereken yanıt, 23 izin verilen yanıt. Riskli kategorilerde recall %100, yanlış pozitif 0. CI'da ayrı ve zorunlu bir adım olarak her push'ta koşar. Test raporu: [`docs/M4/guardrail-raporu.md`](docs/M4/guardrail-raporu.md)

## Güvenlik ve veri gizliliği

Sağlık verisi KVKK kapsamında özel nitelikli kişisel veridir; yaklaşım "privacy by design" ilkesine dayanır ([ayrıntı](docs/M1/03-veri-gizliligi.md)).

| Önlem | Uygulama |
|---|---|
| Rol bazlı erişim | Hasta yalnızca kendini; hekim yalnızca atanmış hastalarını (okuma + uyarı onayı); bakım veren bağlı hastayı salt okunur görür. Tüm kararlar backend'de ([`deps.py`](backend/app/deps.py)). |
| Varlık sızdırmama | Yetkisiz erişimde 403 değil **404** |
| Atamalar | Hekim ve bakım veren ataması hastanın kendisi tarafından yapılır |
| Açık rıza | Hasta kaydı rıza onayı olmadan reddedilir; onay zamanı saklanır |
| Veri minimizasyonu | TC no, adres, telefon toplanmaz; doğum tarihi yerine doğum yılı |
| LLM | Ad / e-posta / kimlik gönderilmez; tool'lara hasta kimliğini sunucu bağlar |
| Oturum | JWT yalnızca Next.js sunucusunda **httpOnly**, `SameSite=Lax` (üretimde `Secure`) çerezde; tarayıcı JS'i token'ı hiç görmez, tüm çağrılar aynı kökenli `/api/proxy` üzerinden |
| Cihaz | Service worker `/api/*` yanıtlarını ve kişiye özel sayfaları **önbelleğe almaz** |
| Erişim kaydı | Hekim/yakının her hasta verisi okuması `access_logs`'a yazılır (sağlık değeri olmadan); hasta kendi erişim geçmişini görür |
| Kötüye kullanım | Giriş, kayıt ve koç uç noktalarında istek sınırı (Redis); 15 dk erişim + döndürmeli yenileme token'ı, çıkışta iptal |
| Diğer | bcrypt şifre hash'i, güvenlik başlıkları, sırlar yalnızca `.env`'de, guardrail olayları içerik olmadan loglanır, yalnızca sentetik veri |

Etik değerlendirme ve zarar senaryoları: [final rapor §11](docs/M5/final-rapor.md#11-etik-ve-güvenlik-değerlendirmesi) · Ölçüt → kanıt eşlemesi: [`etik-guvenlik-kontrol-listesi.md`](docs/M5/etik-guvenlik-kontrol-listesi.md)

## Testler ve CI

```bash
cd backend && pytest -q && ruff check app tests scripts         # SQLite bellek içi, gerçek LLM çağrısı yok
# gerçek altyapı testleri (yoksa atlanır): Lua token bucket, ortak devre kesici, pgvector, advisory lock
REDIS_TEST_URL=redis://localhost:6379/15 TEST_DATABASE_URL=postgresql+psycopg://ilac:ilac@localhost:5433/ilac_kocu pytest -q
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```

```
backend/tests/
├── guardrail_scenarios.yaml  232 red-team senaryosu (beklenen eylemle)
├── coach_eval_scenarios.yaml koç kalite seti: veriye dayalılık, doğru kaynak, eskalasyon, kapsam
├── test_guardrails.py        senaryo seti (parametrik) + çıktı doğrulayıcı
├── test_coach_eval.py        kalite seti (kural tabanlı koç tüm ölçütleri geçmeli) + model karşılaştırma betiği
├── test_adherence.py         uyum skoru (spec örneği: 7 günde 3 kaçırılmış doz)
├── test_api.py               auth, CRUD, rol bazlı erişim (404), koç, openFDA (mock)
├── test_coach_llm.py         Claude tool döngüsü, grounding, bütçe, yedek (sahte istemci)
├── test_rag.py               embedding, arama, seed, openFDA ingest, /education uçları
├── test_rag_postgres.py      pgvector yolu (PostgreSQL gerekir)
├── test_summary_llm.py       haftalık anlatı: LLM, önbellek, şablon yedeği
├── test_push.py              VAPID abonelik, hatırlatma zamanlayıcısı
├── test_drug_catalog.py      ilaç kataloğu, openFDA senkronu, arama, doğrulama
├── test_platform.py          /v1, token bucket, Idempotency-Key, ETag, cursor sayfalama, sağlık uçları
├── test_clinical_insights.py klinik özet: eğilim, hedef aralıkta kalma, atlama nedenleri
├── test_education_review.py  eğitim içeriği hekim onayı, RAG'in reddedileni kullanmaması
└── test_resilience.py        devre kesici, yeniden deneme + DLQ, lider işçi, zamanlanmış haftalık özet
```

**Toplam 608 backend testi** geçer (parametrik senaryolar dahil; gerçek Redis/PostgreSQL ile hiçbiri atlanmaz); `ruff` temizdir. Frontend: `tsc`, `eslint`, `next build` geçer. Testler gerçek Claude'a istek atmaz (sahte istemci). Koçu gerçek Claude'a karşı 232 senaryoyla denemek için: `cd backend && python -m scripts.eval_coach --live`; modelleri kalite setiyle karşılaştırmak için `python -m scripts.eval_llm_models --live --models ...` (API anahtarı gerekir, ücretli; sonuç [`docs/M4/llm-karsilastirma.md`](docs/M4/llm-karsilastirma.md)).

GitHub Actions ([`ci.yml`](.github/workflows/ci.yml)): **backend** (ruff → guardrail seti → koç kalite seti → diğer testler, Redis ve PostgreSQL servisleriyle → tek migration head kontrolü → PostgreSQL'de migration upgrade/downgrade/upgrade → seed) ve **frontend** (lint → tip denetimi → derleme) paralel; ikisi geçerse **docker** işi API ve web imajlarını derler.

## Belgeler

| Belge | İçerik |
|---|---|
| [`docs/PROJE_TANIMI.md`](docs/PROJE_TANIMI.md) | Proje tanımı ve değerlendirme ölçütleri |
| **M1** | |
| [`docs/M1/01-proje-onerisi.md`](docs/M1/01-proje-onerisi.md) | Problem, hedef, kapsam, teknoloji seçimi, riskler |
| [`docs/M1/02-guardrail-politikasi.md`](docs/M1/02-guardrail-politikasi.md) | Koçun asla yapmadıkları, 3 katmanlı kontrol |
| [`docs/M1/03-veri-gizliligi.md`](docs/M1/03-veri-gizliligi.md) | KVKK ilkeleri, erişim matrisi |
| [`docs/M1/04-kullanici-akislari.md`](docs/M1/04-kullanici-akislari.md) | Hasta, hekim, bakım veren akışları |
| [`docs/M1/05-mimari.md`](docs/M1/05-mimari.md) | Bileşen ve sıra diyagramları, tasarım kararları |
| [`docs/er-diyagrami.md`](docs/er-diyagrami.md) | ER diyagramı |
| **M2** | |
| [`docs/M2/README.md`](docs/M2/README.md) | M2 teslim belgeleri |
| [`docs/M2/api-platform.md`](docs/M2/api-platform.md) | `/v1` sürümleme, token bucket, Idempotency-Key, ETag, cursor sayfalama, sağlık uçları |
| **M3** | |
| [`docs/M3/README.md`](docs/M3/README.md) | M3 teslim belgeleri |
| **M4** | |
| [`docs/M4/guardrail-raporu.md`](docs/M4/guardrail-raporu.md) | 232 senaryoluk guardrail test raporu (kategori dağılımı, recall, yanlış pozitif) |
| [`docs/M4/uyum-skoru-dogrulama.md`](docs/M4/uyum-skoru-dogrulama.md) | Uyum skoru elle doğrulaması (3 hata bulundu ve düzeltildi) |
| [`docs/M4/llm-karsilastirma.md`](docs/M4/llm-karsilastirma.md) | Koç kalite setiyle LLM model karşılaştırması (betikle üretilir) |
| **M5** | |
| [`docs/M5/final-rapor.md`](docs/M5/final-rapor.md) | Final rapor: mimari, yapay zekâ, guardrail sonuçları, gizlilik, **etik/güvenlik değerlendirmesi**, **sınırlılıklar** |
| [`docs/M5/kurulum.md`](docs/M5/kurulum.md) | Kurulum talimatı (Windows / macOS / Linux) |
| [`docs/M5/demo-senaryosu.md`](docs/M5/demo-senaryosu.md) | Demo videosu senaryosu ve kayıt kontrol listesi |
| [`docs/M5/etik-guvenlik-kontrol-listesi.md`](docs/M5/etik-guvenlik-kontrol-listesi.md) | Değerlendirme ölçütü → kanıt eşlemesi |
| [`docs/M5/klinik-icgoruler.md`](docs/M5/klinik-icgoruler.md) | Hekim klinik özeti ve eğitim içeriği onayı |
| [`docs/M5/dayaniklilik.md`](docs/M5/dayaniklilik.md) | Devre kesici, yeniden deneme + DLQ, lider işçi, zamanlanmış işler, yapay zekâ değerlendirmesi |
| Demo videosu | <!-- KULLANICI: kayıttan sonra video bağlantısını buraya yazın --> *(henüz yüklenmedi)* |

## Sınırlılıklar

Tam liste: [final rapor §12](docs/M5/final-rapor.md#12-sınırlılıklar).

- **Sentetik veri, klinik doğrulama yok.** Etkinlik ve güvenlik gerçek hastalarla test edilmedi.
- **openFDA ABD etiketlerini döndürür;** Türk ticari isimleri küçük bir eşleme tablosuyla etken maddeye çevrilir.
- **Kural tabanlı guardrail'lerin recall sınırı vardır;** çıktı doğrulayıcı, ölçüm eşikleri ve hekime yönlendirme ikinci savunma hattıdır.
- **Eşik değerleri** genel kabul gören değerlerdir; danışman hekim onayı ve hasta bazlı hedefler gerekir.
- **Hekim hesabı** serbest kayıtla açılır; gerçek kullanımda yönetici onayı gerekir.
- **Tek süreç zamanlayıcı** (APScheduler API içinde); yatay ölçeklemede ayrı worker gerekir.
- **iOS'ta web push** yalnızca 16.4+ ve ana ekrana eklenmiş uygulamada çalışır.
- **Koç yanıt kalitesi** insan/klinisyen değerlendirmesiyle ölçülmedi; `python -m scripts.eval_coach --live` (`backend/scripts/eval_coach.py`) 232 senaryoyu gerçek Claude'a karşı koşabilir (gelecek çalışma).
- **Eğitim içeriği** (31 kürate kayıt) klinisyen tarafından gözden geçirilmedi (`reviewed: false`).
