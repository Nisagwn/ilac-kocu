# ER Diyagramı

```mermaid
erDiagram
    users ||--o| patients : "hasta profili"
    users ||--o| clinicians : "hekim profili"
    clinicians ||--o{ patients : "takip eder"
    patients ||--o{ patient_caregivers : ""
    users ||--o{ patient_caregivers : "bakım veren"
    patients ||--o{ medications : ""
    medications ||--o{ medication_schedules : ""
    medication_schedules ||--o{ dose_logs : ""
    patients ||--o{ dose_logs : ""
    patients ||--o{ measurements : ""
    patients ||--o{ coach_messages : ""
    patients ||--o{ alerts : ""

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
        int user_id FK
        string title
        string institution
    }
    patients {
        int id PK
        int user_id FK
        int clinician_id FK
        int birth_year
        bool has_diabetes
        bool has_hypertension
        datetime consent_at
    }
    patient_caregivers {
        int patient_id PK,FK
        int caregiver_user_id PK,FK
    }
    medications {
        int id PK
        int patient_id FK
        string name
        string active_ingredient
        string dose_text "hekimin yazdığı haliyle"
        bool active
    }
    medication_schedules {
        int id PK
        int medication_id FK
        time time_of_day
        string days_of_week "1234567"
    }
    dose_logs {
        int id PK
        int patient_id FK
        int schedule_id FK
        date scheduled_date
        enum status "taken|skipped"
        string reason
        datetime logged_at
    }
    measurements {
        int id PK
        int patient_id FK
        enum type "glucose_fasting|glucose_postprandial|blood_pressure"
        float value
        float value2 "diyastolik"
        string unit
        datetime measured_at
    }
    coach_messages {
        int id PK
        int patient_id FK
        enum sender "patient|coach"
        text content
        enum risk_level "low|medium|high|emergency"
        bool escalate_to_clinician
        json sources
        string guardrail_action
        datetime created_at
    }
    alerts {
        int id PK
        int patient_id FK
        enum severity "info|warning|critical"
        string kind
        text message
        datetime created_at
        datetime acknowledged_at
    }
    education_contents {
        int id PK
        string title
        text body
        string source_name
        string source_url
        string tags
        vector embedding "pgvector (M4)"
    }
```

`dose_logs` üzerinde `(schedule_id, scheduled_date)` tekil kısıtı vardır: bir planlı doz için gün başına tek kayıt.
