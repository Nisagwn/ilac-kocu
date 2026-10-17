# Geliştirme durumu ve devam planı

> Bu dosya, çalışma kesilirse (oturum kapanırsa, ajan yarıda kalırsa) kaldığı yerden devam etmek içindir.
> Her iş bitince ilgili kutuyu işaretle ve "Günlük" bölümüne bir satır ekle.

Başlangıç: 2026-10-17 · Dal: `gelistirme` (eski repo `ilac-kocu`, `main`'den açıldı) · Başlangıçta 474 backend testi geçiyordu.

## Kurallar
- Commit mesajları Türkçe; **asla** `Co-Authored-By`, "Generated with" ya da Claude/AI imzası yok. Git yazar ayarı değiştirilmez.
- **Push yok.** Ne eski repoya ne yeni repoya (`ilac-uyum-kocu`) kullanıcı onayı olmadan push edilmez.
- Yeni repoya (`Nisagwn/ilac-uyum-kocu`) kod, teslim takvimine göre parça parça taşınır (M2 13 Kas, M3 11 Ara, M4 1 Oca, M5 17 Oca).
- Doğrulama: `cd backend && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check app tests`; `cd frontend && npm run lint && npm run build`.
- Guardrail değişirse `backend/tests/guardrail_scenarios.yaml` güncellenir.

## İş paketleri (paralel ajanlar, her biri ayrı git worktree dalında)

### A — openFDA ilaç kataloğu ve seçimi
- [ ] `drug_catalog` tablosu + migration `0007_drug_catalog`; `medications.catalog_id`, `medications.verified`
- [ ] openFDA `count` ile katalog senkronu (diyabet/hipertansiyon EPC sınıfları) + CLI
- [ ] Kürate TR ticari ad kataloğu `backend/app/data/drug_catalog_tr.yaml` (~60), `resolve_generic` buradan beslenir
- [ ] `GET /drugs/search?q=` (TR ad / generic, Türkçe karakter duyarsız)
- [ ] İlaç formunda otomatik tamamlamalı seçim; "listede yok" → `verified=false` + uyarı; hekim detayında rozet
- [ ] Bakım veren rolünde `/drugs/label` eğitim içeriğine yazmaz
- [ ] `tests/test_drug_catalog.py`

### B — API platform katmanı
- [ ] `/v1` sürümleme (eski yollar geriye dönük, gizli/deprecated); frontend `/v1` kullanır
- [ ] Spec yolları: `GET /v1/medications|doses|measurements?patient_id=` (yetkisiz → 404)
- [ ] Token bucket rate limit (Redis + Lua), `X-RateLimit-*`, `Retry-After`
- [ ] Idempotency-Key + `idempotency_keys` tablosu (migration `0008_idempotency`); frontend anahtar üretir
- [ ] ETag / 304, `Cache-Control: private, no-cache`
- [ ] Cursor sayfalama (ölçüm, doz, uyarı, koç mesajı)
- [ ] `/health/live`, `/health/ready` (DB, Redis, alembic head)
- [ ] `tests/test_platform.py`

### C — Altyapı
- [ ] Nginx reverse proxy + LB (`infra/nginx/`), api × 3, `X-Upstream` başlığı, güvenlik başlıkları, gzip, `limit_req`
- [ ] Graceful shutdown (uvicorn + compose `stop_grace_period`), healthcheck'ler
- [ ] Prometheus + Grafana (pano) + Jaeger, compose profile `observability`; `setup_tracing(app)` (OTel, env ile açılır)
- [ ] k6 senaryoları (`infra/k6/`), `docs/yuk-testi.md` (1 vs 3 kopya, kopya durdurma)
- [ ] `infra/README.md`

### D — Dayanıklılık, worker, yapay zekâ değerlendirmesi, CI
- [ ] Circuit breaker (`services/circuit_breaker.py`, Redis'te durum) — LLM'e uygulanır
- [ ] DLQ (3 deneme + backoff), DLQ CLI ve metriği
- [ ] PostgreSQL advisory lock ile lider worker
- [ ] Worker graceful shutdown
- [ ] Pazartesi 07:00 zamanlanmış haftalık özet (+ gerekirse migration `0009_weekly_summaries`), hekime bildirim
- [ ] Koç kalite değerlendirme seti `tests/coach_eval_scenarios.yaml` + `tests/test_coach_eval.py`
- [ ] LLM karşılaştırma betiği `backend/scripts/eval_llm_models.py` → `docs/M4/llm-karsilastirma.md` (gerçek API'ye karşı kullanıcı onayıyla koşulacak)
- [ ] pgvector üzerinde RAG testi (`pytest.mark.postgres`)
- [ ] CI: adım adı ("40 senaryo" kaldır), guardrail testlerinin çift koşmaması, yeni adımlar

### E — Klinik içgörüler ve eğitim içeriği onayı
- [ ] `services/clinical_insights.py`: uyum eğilimi, hedef aralıkta kalma oranı, doz atlama nedenleri, ölçüm sıklığı
- [ ] `GET /patients/{id}/insights`; hasta listesinde eğilim
- [ ] Hekim arayüzü: liste eğilim oku, detayda "Klinik özet" kartı
- [ ] Eğitim içeriği onay ekranı `/hekim/egitim`, onay/ret uçları (+ gerekirse migration `0010_education_review`), RAG reddedileni kullanmaz
- [ ] `POST /education/ingest-drug` bakım verene kapalı
- [ ] `tests/test_clinical_insights.py`, `tests/test_education_review.py`

## Birleştirme planı (ajanlar bittikten sonra)
1. Her ajan dalını `gelistirme`'ye sırayla birleştir: **A → B → E → D → C** (C en çok compose/main.py'ye dokunur, en son).
2. **Migration zinciri**: ajanların hepsi `down_revision="0006_security"` yazdı. Birleştirirken zincirle:
   `0006_security → 0007_drug_catalog → 0008_idempotency → 0009_weekly_summaries (varsa) → 0010_education_review (varsa)`.
   Sonra `alembic heads` tek head göstermeli; PostgreSQL'de `upgrade head`, `downgrade -1`, `upgrade head` dene.
3. Çakışma beklenen dosyalar: `backend/app/main.py`, `backend/app/models.py`, `backend/app/config.py`, `backend/app/observability.py`, `backend/app/worker.py`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/clinician/patient-detail.tsx`, `docker-compose.yml`, `backend/requirements.txt`.
4. openFDA çağrısını D'nin circuit breaker'ıyla sar (A'nın `openfda.py`'si üzerine).
5. B'nin idempotency temizlik fonksiyonunu D'nin worker zamanlayıcısına bağla.
6. Tüm testler + ruff + frontend lint/build; `docker compose up --build` ile uçtan uca deneme (giriş, ilaç arama/ekleme, doz, koç, hekim paneli).
7. Belgeler: `docs/er-diyagrami.md` güncelle (yeni tablolar: drug_catalog, idempotency_keys, weekly_summaries?, coach_jobs, refresh_tokens, access_logs), README'ye ER diyagramını göm, README "Durum" tablosu.

## Kalan (bu turun dışında)
- Demo videosu (M5) — kullanıcı çekecek.
- Final raporda "AI Tools Usage" bölümü — kullanıcı yazacak (`ilac-uyum-kocu/docs/ai-araclari-kullanimi.md` kaydından).
- Gerçek LLM model karşılaştırması — ücretli API, kullanıcı onayıyla.
- Yeni repoya taşıma: her teslimde ilgili parça.

## Günlük
- 2026-10-17: `gelistirme` dalı açıldı (474 test geçti). A, B, C, D, E ajanları paralel başlatıldı.
- 2026-10-17: A-E iş paketlerinin her birinde ayrı worktree'de kod değişiklikleri var; henüz birleştirilmediler ve bu nedenle tamamlanmış sayılmıyorlar. Birleştirme, migration zinciri ve doğrulama adımları yukarıdaki plana göre yapılacak.
