# ER Diyagramı

```mermaid
erDiagram
    users ||--o| clinicians : "user_id"
    patients ||--o{ patient_caregivers : "patient_id"
    users ||--o{ patient_caregivers : "caregiver_user_id"
    users ||--o| patients : "user_id"
    clinicians |o--o{ patients : "clinician_id"
    patients ||--o{ medications : "patient_id"
    drug_catalog |o--o{ medications : "catalog_id"
    medications ||--o{ medication_schedules : "medication_id"
    patients ||--o{ dose_logs : "patient_id"
    medication_schedules ||--o{ dose_logs : "schedule_id"
    medication_schedules ||--o{ reminder_logs : "schedule_id"
    users ||--o{ push_subscriptions : "user_id"
    patients ||--o{ measurements : "patient_id"
    patients ||--o{ coach_messages : "patient_id"
    patients ||--o{ alerts : "patient_id"
    users |o--o{ education_contents : "reviewed_by"
    patients |o--o{ llm_usage : "patient_id"
    patients ||--o{ coach_jobs : "patient_id"
    users ||--o{ refresh_tokens : "user_id"
    users |o--o{ access_logs : "actor_user_id"
    patients ||--o{ access_logs : "patient_id"
    users ||--o{ idempotency_keys : "user_id"
    patients ||--o{ weekly_summaries : "patient_id"

    users {
        int id PK
        string email UK
        string password_hash
        string full_name
        enum role "patient|clinician|caregiver"
        datetime created_at
    }
    clinicians {
        int id PK
        int user_id FK,UK
        string title
        string institution
    }
    patient_caregivers {
        int patient_id PK,FK
        int caregiver_user_id PK,FK
    }
    patients {
        int id PK
        int user_id FK,UK
        int clinician_id FK
        int birth_year
        bool has_diabetes
        bool has_hypertension
        datetime consent_at
    }
    medications {
        int id PK
        int patient_id FK
        string name
        string active_ingredient
        string dose_text "hekimin yazdığı haliyle"
        string notes
        bool active
        datetime created_at
        int catalog_id FK
        bool verified "katalogdan seçildi mi"
    }
    drug_catalog {
        int id PK
        string generic_name UK
        string display_name
        json brand_names
        json tr_brand_names
        string pharm_class
        string source "tr_curated|openfda"
        bool openfda_available
        string search_text
        datetime updated_at
    }
    medication_schedules {
        int id PK
        int medication_id FK
        time time_of_day
        string days_of_week
        date start_date
    }
    dose_logs {
        int id PK
        int patient_id FK
        int schedule_id FK
        date scheduled_date
        enum status
        string reason
        datetime logged_at
    }
    reminder_logs {
        int id PK
        int schedule_id FK
        date scheduled_date
        datetime sent_at
    }
    push_subscriptions {
        int id PK
        int user_id FK
        string endpoint UK
        string p256dh
        string auth
        datetime created_at
    }
    measurements {
        int id PK
        int patient_id FK
        enum type
        float value
        float value2
        string unit
        string note
        datetime measured_at
    }
    coach_messages {
        int id PK
        int patient_id FK
        enum sender
        string content
        enum risk_level
        bool escalate_to_clinician
        json sources
        string guardrail_action
        datetime created_at
    }
    alerts {
        int id PK
        int patient_id FK
        enum severity
        string kind
        string message
        datetime created_at
        datetime acknowledged_at
    }
    education_contents {
        int id PK
        string title
        string body
        string source_name
        string source_url
        string tags
        datetime created_at
        string kind
        string drug_key UK
        bool reviewed
        string review_status "pending|approved|rejected"
        int reviewed_by FK
        datetime reviewed_at
        vector embedding "pgvector(384)"
        string embedding_model
    }
    llm_usage {
        int id PK
        int patient_id FK
        string purpose
        string model
        int input_tokens
        int output_tokens
        int cache_read_tokens
        int cache_write_tokens
        float cost_usd
        datetime created_at
    }
    coach_jobs {
        string id PK
        int patient_id FK
        string message
        string status "queued|running|done|failed"
        json reply
        string error
        int attempts
        datetime created_at
        datetime started_at
        datetime finished_at
    }
    refresh_tokens {
        int id PK
        int user_id FK
        string token_hash UK
        datetime expires_at
        datetime revoked_at
        datetime created_at
        int replaced_by
    }
    access_logs {
        int id PK
        int actor_user_id FK
        string actor_role
        int patient_id FK
        string action
        string path
        datetime created_at
    }
    idempotency_keys {
        int id PK
        int user_id FK
        string key
        string request_hash "aynı anahtar + farklı gövde → 422"
        int response_status
        json response_body
        datetime created_at
    }
    weekly_summaries {
        int id PK
        int patient_id FK
        date period_start
        date period_end
        json data
        string narrative_source
        datetime generated_at
    }
```

`dose_logs` üzerinde `(schedule_id, scheduled_date)` tekil kısıtı vardır: bir planlı doz için gün başına tek kayıt.

`reminder_logs` üzerinde `(schedule_id, scheduled_date)` tekil kısıtı vardır: bir planlı doz için gün başına en fazla bir web push hatırlatması. `push_subscriptions.endpoint` tekildir (aynı tarayıcı yeniden abone olursa kayıt güncellenir); push servisi 404/410 dönerse abonelik silinir.

`llm_usage` her Claude çağrısının (koç ve haftalık özet anlatısı) token ve tahmini USD maliyetini tutar (migration `0004_llm_usage`); mesaj içeriği saklanmaz. Günlük hasta isteği/token limiti ve aylık USD bütçesi bu tablodan hesaplanır (Redis kullanılmaz). Hasta silinirse `patient_id` NULL olur, kayıt anonim toplam olarak kalır.

`education_contents` RAG korpusudur (migration `0003_rag`): `kind='curated'` satırlar `backend/app/data/education.yaml` dosyasından seed ile başlığa göre upsert edilir; `kind='openfda'` satırlar openFDA etiketinden üretilir ve `drug_key` (etken madde) tekildir. `embedding` PostgreSQL'de pgvector `vector(384)` (HNSW, `vector_cosine_ops`), SQLite'ta (testler) JSON'dur; `embedding_model` farklıysa satır tembel olarak yeniden embed edilir.

### Sonradan eklenen tablolar ve alanlar

- `drug_catalog` (migration `0007_drug_catalog`): ilaç seçim kataloğu. `tr_curated` satırları `backend/app/data/drug_catalog_tr.yaml`'dan seed ile gelir (Türk ticari adları → etken madde); `openfda` satırları isteğe bağlı `python -m app.scripts.sync_drug_catalog` ile openFDA `count` uç noktasından çekilir. `search_text` Türkçe karakter ve büyük/küçük harf duyarsız arama içindir. `medications.catalog_id` + `medications.verified`: katalogdan seçilen ilaç doğrulanmıştır; listede olmayan ilaç `verified=false` kaydedilir ve hekim panelinde işaretlenir.
- `idempotency_keys` (migration `0008_idempotency`): `(user_id, key)` tekil. Aynı `Idempotency-Key` + aynı gövde → kayıtlı yanıt (`Idempotent-Replayed: true`); farklı gövde → 422. 24 saatten eski kayıtlar worker'daki günlük işle silinir.
- `education_contents.review_status / reviewed_by / reviewed_at` (migration `0010_education_review`): hekim onayı. Reddedilen içerik RAG'de kullanılmaz; onay bekleyen openFDA içeriği kaynakta işaretlenir (bkz. `docs/egitim-icerigi-onayi.md`).
- `weekly_summaries` (migration `0011_weekly_summaries`): her pazartesi lider worker'ın ürettiği haftalık özet; `/summaries` önce son saklanan özeti döner.
- `coach_jobs` (0005), `refresh_tokens` ve `access_logs` (0006): koç iş kuyruğu, oturum yenileme token'ları (yalnız SHA-256 özeti) ve KVKK erişim kaydı.
