# Geliştirme durumu ve devam planı

> Bu dosya, çalışma kesilirse (oturum kapanırsa, ajan yarıda kalırsa) kaldığı yerden devam etmek içindir.
> Her iş bitince ilgili kutuyu işaretle ve "Günlük" bölümüne bir satır ekle.

Başlangıç: 2026-10-17 · Dal: `gelistirme` (eski repo `ilac-kocu`, `main`'den açıldı) · Başlangıçta 474 backend testi geçiyordu.

## Kurallar
- Commit mesajları Türkçe; **asla** `Co-Authored-By`, "Generated with" ya da Claude/AI imzası yok. Git yazar ayarı değiştirilmez.
- **Push:** yalnızca `gelistirme` dalına, her iş paketi bitince (kullanıcı onayı 2026-10-17). `main`'e ve yeni repoya (`ilac-uyum-kocu`) onaysız push yok.
- Yeni repoya (`Nisagwn/ilac-uyum-kocu`) kod, teslim takvimine göre parça parça taşınır (M2 13 Kas, M3 11 Ara, M4 1 Oca, M5 17 Oca).
- Doğrulama: `cd backend && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check app tests`; `cd frontend && npm run lint && npm run build`.
- Guardrail değişirse `backend/tests/guardrail_scenarios.yaml` güncellenir.

## İş paketleri (sırayla, doğrudan `gelistirme` dalında; her paket bitince commit + push)

### A — openFDA ilaç kataloğu ve seçimi
- [x] `drug_catalog` tablosu + migration `0007_drug_catalog`; `medications.catalog_id`, `medications.verified`
- [x] openFDA `count` ile katalog senkronu (diyabet/hipertansiyon EPC sınıfları) + CLI
- [x] Kürate TR ticari ad kataloğu `backend/app/data/drug_catalog_tr.yaml` (46 etken madde / 53 ticari ad; yalnızca emin olunan adlar), `resolve_generic` buradan beslenir
- [x] `GET /drugs/search?q=` (TR ad / generic, Türkçe karakter duyarsız)
- [x] İlaç formunda otomatik tamamlamalı seçim; "listede yok" → `verified=false` + uyarı; hekim detayında rozet
- [x] Bakım veren rolünde `/drugs/label` eğitim içeriğine yazmaz
- [x] `tests/test_drug_catalog.py`

### B — API platform katmanı
- [x] `/v1` sürümleme (eski yollar geriye dönük, gizli/deprecated); frontend `/v1` kullanır
- [x] Spec yolları: `GET /v1/medications|doses|measurements?patient_id=` (yetkisiz → 404)
- [x] Token bucket rate limit (Redis + Lua), `X-RateLimit-*`, `Retry-After`
- [x] Idempotency-Key + `idempotency_keys` tablosu (migration `0008_idempotency`); frontend anahtar üretir
- [x] ETag / 304, `Cache-Control: private, no-cache`
- [x] Cursor sayfalama (ölçüm, doz, uyarı, koç mesajı)
- [x] `/health/live`, `/health/ready` (DB, Redis, alembic head)
- [x] `tests/test_platform.py`

### C — Altyapı
- [x] Nginx reverse proxy + LB (`infra/nginx/`), api × 3, `X-Upstream` başlığı, güvenlik başlıkları, gzip, `limit_req`
- [x] Graceful shutdown (uvicorn + compose `stop_grace_period`), healthcheck'ler
- [x] Prometheus + Grafana (pano) + Jaeger, compose profile `observability`; `setup_tracing(app)` (OTel, env ile açılır)
- [x] k6 senaryoları (`infra/k6/`), `docs/yuk-testi.md` (1 vs 3 kopya, kopya durdurma)
- [x] `infra/README.md`

### D — Dayanıklılık, worker, yapay zekâ değerlendirmesi, CI
- [x] Circuit breaker (`services/circuit_breaker.py`, Redis'te durum) — LLM'e uygulanır
- [x] DLQ (3 deneme + backoff), DLQ CLI ve metriği
- [x] PostgreSQL advisory lock ile lider worker
- [x] Worker graceful shutdown
- [x] Pazartesi 07:00 zamanlanmış haftalık özet (+ gerekirse migration `0010_weekly_summaries`), hekime bildirim
- [x] Koç kalite değerlendirme seti `tests/coach_eval_scenarios.yaml` + `tests/test_coach_eval.py`
- [x] LLM karşılaştırma betiği `backend/scripts/eval_llm_models.py` → `docs/M4/llm-karsilastirma.md` (şimdilik yalnızca kural tabanlı taban çizgisi; gerçek API'ye karşı kullanıcı onayıyla koşulacak)
- [x] pgvector üzerinde RAG testi (`pytest.mark.postgres`) — reddedilen içeriğin `_pg_candidates`'ta elendiği de test edilsin
- [x] CI: adım adı ("40 senaryo" kaldır), guardrail testlerinin çift koşmaması, yeni adımlar

### E — Klinik içgörüler ve eğitim içeriği onayı
- [x] `services/clinical_insights.py`: uyum eğilimi, hedef aralıkta kalma oranı, doz atlama nedenleri, ölçüm sıklığı
- [x] `GET /patients/{id}/insights`; hasta listesinde eğilim
- [x] Hekim arayüzü: liste eğilim oku, detayda "Klinik özet" kartı
- [x] Eğitim içeriği onay ekranı `/hekim/egitim`, onay/ret uçları (+ gerekirse migration `0009_education_review`), RAG reddedileni kullanmaz
- [x] `POST /education/ingest-drug` bakım verene kapalı
- [x] `tests/test_clinical_insights.py`, `tests/test_education_review.py`

## Sıra ve entegrasyon notları
1. Paket sırası: **A → B → E → D → C** (C en çok compose/main.py'ye dokunur, en son).
2. **Migration zinciri** (her paket bir öncekinin üstüne yazar):
   `0006_security → 0007_drug_catalog → 0008_idempotency → 0009_education_review → 0010_weekly_summaries (D, gerekirse)`.
   (E, D'den önce yapıldığı için numaralar plana göre yer değiştirdi.)
   Her migration'dan sonra `alembic heads` tek head göstermeli; PostgreSQL'de `upgrade head`, `downgrade -1`, `upgrade head`.
3. ~~Çakışma beklenen dosyalar~~ (paralel ajan yok; geçersiz).
4. ~~openFDA çağrısını D'nin circuit breaker'ıyla sar~~ (D'de yapıldı).
5. ~~Idempotency temizliğini lider işçi zamanlayıcısına bağla~~ (D'de yapıldı: `idempotency_purge` saatlik).
5b. ~~CI'a Redis servisi + `REDIS_TEST_URL`~~ (D'de yapıldı; ayrıca `TEST_DATABASE_URL`).
6. ~~Uçtan uca deneme~~ yapıldı: Docker yığınında (api×3 + nginx + işçi) tarayıcıyla giriş, doz, katalogdan ilaç ekleme, koç (kuyruk + işçi), acil yanıt, hekim paneli + klinik özet, kritik uyarı, eğitim içeriği onayı.
7. ~~Belgeler~~ yapıldı: ER diyagramı güncellendi ve README'ye gömüldü; README mimari/test/belge tabloları.

## Kalan (bu turun dışında)
- `GET /v1/patients` (hekim listesi) hasta başına uyumu birkaç kez hesaplıyor; yük testinde istek başına ~75 ms CPU (docs/yuk-testi.md). Büyük listeler için önbellek / toplu hesaplama.
- `docs/gorseller/12-mimari.png` eski mimariyi gösteriyor (nginx, kopyalar, lider işçi yok); README'deki mermaid şeması güncel.
- Demo videosu (M5) — kullanıcı çekecek.
- Final raporda "AI Tools Usage" bölümü — kullanıcı yazacak (`ilac-uyum-kocu/docs/ai-araclari-kullanimi.md` kaydından).
- Gerçek LLM model karşılaştırması — ücretli API, kullanıcı onayıyla.
- Yeni repoya taşıma: her teslimde ilgili parça.

## Günlük
- 2026-10-17: `gelistirme` dalı açıldı (474 test geçti). A, B, C, D, E ajanları paralel başlatıldı.
- 2026-10-17: A-E iş paketlerinin her birinde ayrı worktree'de kod değişiklikleri var; henüz birleştirilmediler ve bu nedenle tamamlanmış sayılmıyorlar. Birleştirme, migration zinciri ve doğrulama adımları yukarıdaki plana göre yapılacak.
- 2026-10-17: Oturum kesildi; worktree dalları push edilmediği için A-E kodu kayboldu. Paketler sırayla, doğrudan `gelistirme` dalında yeniden yazılıyor; her paket sonunda push.
- 2026-10-17: **A bitti.** 496 backend testi geçti, ruff temiz; frontend lint/tsc/build temiz. PostgreSQL'de `0007_drug_catalog` upgrade/downgrade/upgrade ve seed denendi; tarayıcıda ilaç ekleme (öneri seçimi, listede olmayan ilaç uyarısı, hekim rozeti) doğrulandı.
- 2026-10-17: **B bitti.** 529 backend testi geçti (gerçek Redis'le Lua testi dahil), ruff temiz; frontend lint/tsc/build temiz. PostgreSQL'de `0008_idempotency` upgrade/downgrade/upgrade denendi. Tarayıcıda: tüm trafik `/v1`'den, ikinci ziyarette 304, doz işaretlemede Idempotency-Key gidiyor, `/health/ready` Redis+DB+migration "ok". Koça mesaj genel istek sınırından muaf (acil yanıt asla engellenmez). Belge: `docs/M2/api-platform.md`.
- 2026-10-17: **E bitti.** 560 backend testi geçti, ruff temiz; frontend lint/tsc/build temiz. PostgreSQL'de `0009_education_review` upgrade/downgrade/upgrade ve temiz seed denendi (seed artık atlanan dozlara örnek neden yazar; ayrı rastgele üreteç, diğer demo sayıları değişmedi). Tarayıcıda: hasta listesinde eğilim okları, detayda Klinik özet kartı, `/hekim/egitim`'de onay/ret/geri al. Belge: `docs/M5/klinik-icgoruler.md`.
- 2026-10-17: **D bitti.** 608 backend testi geçti (gerçek Redis + PostgreSQL ile, atlanan yok), ruff (app, tests, scripts) temiz. PostgreSQL'de `0010_weekly_summaries` upgrade/downgrade/upgrade. İki gerçek işçiyle lider devri denendi (A'ya SIGTERM → B 2 sn'de lider). Davranış değişiklikleri: `COACH_JOB_MAX_ATTEMPTS` 2 → 3 (spec), hekim artık push aboneliği açabilir (haftalık özet bildirimi; bakım veren hâlâ 403), zamanlayıcı `app/scheduler.py`'ye taşındı. Belge: `docs/M5/dayaniklilik.md`.
- 2026-10-17: **C bitti — beş paketin hepsi tamam.** 611 backend testi geçti (gerçek Redis + PostgreSQL), ruff temiz; frontend lint/tsc/build temiz. Docker yığını bu ortamda gerçekten çalıştırıldı (registry mirror + vekil CA'sı yalnızca yerel derleme sarmalayıcısında; proje Dockerfile'ları değişmedi). Ölçümler `docs/yuk-testi.md`: 1 → 3 kopya verim 67 → 193 istek/sn, p95 945 → 296 ms; kopya durdurmada 902 istekte 0 (SIGTERM) / 1 (SIGKILL) hata; iki katmanlı istek sınırı doğrulandı. Bulgular üzerine: nginx 429'u JSON + Retry-After, `proxy_next_upstream_tries` 3, Grafana panosu sağlık kontrollerini hariç tutar.
