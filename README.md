# İlaç Uyumu ve Yaşam Tarzı Koçu

Tip 2 diyabet ve hipertansiyon hastalarının ilaç ve ölçüm takibini yaptığı, LLM koçun **tıbbi tavsiye vermeden** kişiselleştirilmiş ve güvenli geri bildirim verdiği, hekim için uyum dashboard'u sunan sağlık platformu.

> ⚠️ Koç teşhis koymaz ve ilaç dozu önermez. Acil durumlarda **112**'yi arayın. Demo yalnızca sentetik veriyle çalışır.

## Durum

| Teslim | Tarih | Durum |
|---|---|---|
| M1 — Proje önerisi ve teknoloji seçimi | 23 Eki 2026 | ✅ `docs/M1/` |
| M2 — Veritabanı ve backend iskeleti | 13 Kas 2026 | ✅ `backend/` (84 test) |
| M3 — Arayüz ve dashboard (MVP) | 11 Ara 2026 | ⏳ `frontend/` |
| M4 — Yapay zekâ bileşeni | 1 Oca 2027 | ⏳ koç sağlayıcı arayüzü hazır |
| M5 — Final teslim | 17 Oca 2027 | ⏳ |

## Hızlı başlangıç

```bash
cp .env.example .env          # JWT_SECRET'ı değiştirin
docker compose up --build     # PostgreSQL + Redis + API, migration otomatik
docker compose exec api python -m app.seed   # demo verisi
```

- Swagger: http://localhost:8000/docs
- Demo hesaplar (şifre `demo12345`): `hasta@demo.local`, `hekim@demo.local`, `yakin@demo.local`

### Docker olmadan (geliştirme)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://ilac:ilac@localhost:5432/ilac_kocu
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload
pytest                          # testler SQLite bellek içi veritabanında koşar
```

## Mimari

```mermaid
flowchart LR
    PWA[Next.js PWA<br/>hasta · hekim · bakım veren] --> API[FastAPI<br/>JWT + roller]
    API --> DB[(PostgreSQL + pgvector)]
    API --> GR[Guardrail<br/>girdi / çıktı]
    GR --> LLM[Claude API<br/>tool use]
    LLM -- tool --> ADH[Uyum skoru]
    API --> FDA[openFDA]
    API --> R[(Redis)]
```

Ayrıntı: [`docs/M1/05-mimari.md`](docs/M1/05-mimari.md)

## API özeti

| Uç nokta | Açıklama | Rol |
|---|---|---|
| `POST /auth/register`, `/auth/login`, `GET /auth/me` | Kayıt (hasta için açık rıza zorunlu), JWT | herkes |
| `GET /patients` | Hekim: hasta listesi (kritik uyarı + düşük uyum önce) | tümü |
| `GET /patients/{id}/adherence?days=7` | Uyum skoru (alınan / planlanan doz) | okuma |
| `GET /patients/{id}/summary` | Haftalık özet | okuma |
| `/patients/{id}/medications` | İlaç + zamanlama CRUD (silme = pasife alma) | hasta yazar |
| `GET /patients/{id}/doses/today`, `PUT /patients/{id}/doses` | Bugün ekranı, "aldım/almadım" | hasta yazar |
| `/patients/{id}/measurements` | Kan şekeri / tansiyon; eşik dışı değer → uyarı | hasta yazar |
| `GET /alerts`, `POST /alerts/{id}/ack` | Hekimin açık uyarıları | hekim |
| `GET /drugs/label?name=Glifor` | openFDA etiketi (TR ticari isim → etken madde) | tümü |
| `POST /coach/chat`, `GET /coach/history` | Koç (guardrail'li) | hasta |

## ER diyagramı

[`docs/er-diyagrami.md`](docs/er-diyagrami.md) — `users`, `patients`, `clinicians`, `patient_caregivers`, `medications`, `medication_schedules`, `dose_logs`, `measurements`, `coach_messages`, `alerts`, `education_contents`.

## Güvenlik ve guardrail'ler

Koçun güvenliği LLM'e bırakılmaz; üç katmanlıdır ([politika](docs/M1/02-guardrail-politikasi.md)):

1. **Girdi filtresi (LLM'den önce, deterministik):** acil belirti → sabit 112 yanıtı + hekime kritik uyarı; doz/ilaç değişikliği sorusu → sabit güvenli yanıt.
2. **LLM:** kısıtlı system prompt, tool-use, JSON şeması (`message`, `risk_level`, `escalate_to_clinician`, `sources`).
3. **Çıktı doğrulayıcı (deterministik):** doz önerisi, teşhis veya "ben doktorum" ifadesi içeren yanıt engellenir.

Türkçe karakter ve büyük/küçük harf farkları normalize edilir ("GÖĞSÜM", "gogsum" aynı yakalanır).
**Red-team seti:** [`backend/tests/guardrail_scenarios.yaml`](backend/tests/guardrail_scenarios.yaml) — 40 senaryo (14 acil, 3 kendine zarar, 13 doz sorusu, 10 olağan sohbet / yanlış pozitif kontrolü). CI'da her push'ta koşar.

## Veri gizliliği

- Rol bazlı erişim: hasta yalnızca kendini; hekim yalnızca atanmış hastalarını (okuma); bakım veren bağlı hastayı (salt okunur) görür. Yetkisiz erişimde kaynağın varlığı sızdırılmaz (404).
- Hekim ve bakım veren ataması **hastanın kendisi** tarafından yapılır.
- LLM'e ad/e-posta gönderilmez; tool'lara hasta kimliğini sunucu bağlar.
- Ayrıntı: [`docs/M1/03-veri-gizliligi.md`](docs/M1/03-veri-gizliligi.md)

## Testler ve CI

```
backend/tests/
├── test_guardrails.py      40 senaryo + çıktı doğrulayıcı
├── test_adherence.py       uyum skoru (spec örneği: 7 günde 3 kaçırılmış doz)
└── test_api.py             auth, CRUD, rol bazlı erişim, koç, openFDA (mock)
```

GitHub Actions: ruff → guardrail seti → tüm testler → PostgreSQL'de migration upgrade/downgrade → seed → Docker build.

## Sınırlılıklar

- openFDA ABD etiketlerini döndürür; Türk ticari isimleri küçük bir eşleme tablosuyla etken maddeye çevrilir.
- Kural tabanlı guardrail'ler her ifadeyi yakalayamaz; bu yüzden çıktı doğrulayıcı ve hekime yönlendirme ikinci savunma hattıdır.
- Eşik değerleri genel kabul gören değerlerdir, danışman hekim tarafından doğrulanmalıdır.
- Hekim hesabı şu an serbest kayıtla açılır; gerçek kullanımda yönetici onayı gerekir.
