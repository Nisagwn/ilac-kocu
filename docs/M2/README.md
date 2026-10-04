# M2 Teslimi: Veritabanı + Backend

> Spec (docs/PROJE_TANIMI.md, M2): **"Veritabanı + Backend: Şema + migration, ilaç/doz/ölçüm CRUD, openFDA'dan ilaç etiketi çeken uç nokta, Swagger."**
>
> Bu klasör M2 çıktısını belgeler. Kod M2'de yazıldı; sonraki kilometre taşları (M3 push/hatırlatma, M4 RAG + koç + maliyet kaydı) şemaya ve API'ye ekleme yaptı. Belgelerde "M2 sonrası" etiketiyle ayrılmıştır.

## 1. Kapsam ve teslim edilenler

| Spec maddesi | Durum | Kanıt (dosya yolu) |
|---|---|---|
| Şema (tablolar, ilişkiler, kısıtlar) | Tamam | `backend/app/models.py`, [veritabani-ve-migration.md](veritabani-ve-migration.md), [../er-diyagrami.md](../er-diyagrami.md) |
| Migration (Alembic) | Tamam | `backend/alembic/versions/0001_initial_schema.py` (M2); `0002`, `0003_rag`, `0004_llm_usage` sonraki kilometre taşlarında eklendi |
| Migration CI'da doğrulanıyor | Tamam | `.github/workflows/ci.yml` (PostgreSQL + pgvector üzerinde `upgrade head` / `downgrade base` / `upgrade head`) |
| İlaç CRUD (zamanlamalarla) | Tamam | `backend/app/routers/medications.py`, `backend/app/schemas.py` |
| Doz CRUD (bugün, işaretle, geçmiş) | Tamam | `backend/app/routers/doses.py` |
| Ölçüm CRUD + eşik uyarısı | Tamam | `backend/app/routers/measurements.py`, `backend/app/services/thresholds.py` |
| Kimlik doğrulama + rol bazlı erişim | Tamam | `backend/app/security.py`, `backend/app/deps.py`, `backend/app/routers/auth.py` |
| Uyum skoru uç noktası | Tamam | `backend/app/routers/patients.py` (`/adherence`), `backend/app/services/adherence.py`, [../M4/uyum-skoru-dogrulama.md](../M4/uyum-skoru-dogrulama.md) |
| openFDA'dan ilaç etiketi çeken uç nokta | Tamam | `backend/app/routers/drugs.py`, `backend/app/services/openfda.py`, [openfda-entegrasyonu.md](openfda-entegrasyonu.md) |
| Swagger / OpenAPI | Tamam | FastAPI otomatik üretir: `http://localhost:8000/docs` (ReDoc: `/redoc`, şema: `/openapi.json`) |
| API testleri | Tamam | `backend/tests/test_api.py` |

![Swagger / OpenAPI](../gorseller/11-swagger.png)

## 2. Nasıl çalıştırılır

Gereksinim: Docker Desktop. Depo kökünde `.env` dosyası (örnek: `.env.example`) bulunmalı; en az `JWT_SECRET` tanımlı olmalıdır.

```bash
docker compose up --build
```

- `db`: `pgvector/pgvector:pg16` (PostgreSQL 16 + pgvector), `redis`: `redis:7-alpine` (openFDA önbelleği).
- `api` konteyneri başlarken `alembic upgrade head` çalıştırır, ardından uvicorn'u açar (`backend/Dockerfile`).
- Demo veri (isteğe bağlı): `docker compose exec api python -m app.seed` (örnek hesaplar `hasta@demo.local`, `hekim@demo.local`, `yakin@demo.local`; şifre `demo12345`).

| Adres | Ne |
|---|---|
| http://localhost:8000/docs | Swagger UI (OpenAPI) |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health | Sağlık kontrolü (`{"status":"ok"}`) |
| http://localhost:3000 | Web arayüzü (Next.js) |

Swagger'da yetkilendirme: sağ üstteki **Authorize** düğmesine e-posta (username alanı) ve şifreyi girin; Swagger `/auth/login` ile token alır. Ayrıntı: [api-referansi.md](api-referansi.md).

Yerel (Docker'sız) geliştirme:

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head            # DATABASE_URL tanımlı olmalı
uvicorn app.main:app --reload
pytest -q
```

## 3. Test sonuçları

Güncel tam backend paketi: **395 test geçiyor** (`pytest -q`, `backend/tests/`). CI'da ayrıca lint (`ruff check app tests`), guardrail red-team seti ve PostgreSQL üzerinde migration turu çalışır.

M2 döneminin API testleri (`backend/tests/test_api.py`), kapsam başlıklarıyla:

| Alan | Testler |
|---|---|
| Kimlik doğrulama | `test_register_login_me`, `test_patient_requires_consent`, `test_duplicate_email`, `test_wrong_password_and_no_token`, `test_me_with_seed_style_local_email` |
| İlaç CRUD | `test_medication_crud` |
| Doz + uyum | `test_dose_logging_and_adherence`, `test_dose_validation` |
| Ölçüm + uyarı | `test_measurements_and_threshold_alert` |
| Rol bazlı erişim | `test_patient_cannot_see_other_patient`, `test_clinician_access_flow`, `test_other_clinician_cannot_access`, `test_caregiver_read_only` |
| openFDA | `test_openfda_parse_with_mock_transport`, `test_openfda_not_found`, `test_drug_label_endpoint` |
| Koç (M4 ile genişledi) | `test_coach_emergency_creates_critical_alert`, `test_coach_refuses_dose_change`, `test_coach_normal_reply_has_sources_and_history`, `test_coach_output_guardrail_blocks_bad_provider`, `test_rule_based_coach_cites_relevant_education` |

Testler SQLite bellek içi veritabanıyla hızlı çalışır; PostgreSQL'e özgü kısımlar (pgvector, enum tipleri) CI'daki migration turunda doğrulanır. openFDA çağrıları testlerde `httpx.MockTransport` / `monkeypatch` ile taklit edilir; test sırasında dış ağa çıkılmaz.

## 4. Belgeler indeksi

| Belge | İçerik |
|---|---|
| [veritabani-ve-migration.md](veritabani-ve-migration.md) | Tablo tablo şema, ER diyagramı, migration zinciri, tasarım kararları |
| [api-referansi.md](api-referansi.md) | Tüm uç noktalar, kimlik doğrulama, RBAC, doğrulama kuralları, örnek istek/yanıtlar, sıra diyagramları |
| [openfda-entegrasyonu.md](openfda-entegrasyonu.md) | openFDA alanları, TR ticari isim eşlemesi, hata yönetimi, önbellek, sınırlılıklar |
| [../er-diyagrami.md](../er-diyagrami.md) | ER diyagramının bağımsız sürümü |
| [../M1/05-mimari.md](../M1/05-mimari.md) | Sistem mimarisi |
| [../M1/03-veri-gizliligi.md](../M1/03-veri-gizliligi.md) | Erişim kuralları ve veri gizliliği |
| [../M4/uyum-skoru-dogrulama.md](../M4/uyum-skoru-dogrulama.md) | Uyum skoru tanımı ve doğrulaması |
