# Mimari

```mermaid
flowchart LR
    subgraph Client["İstemci (Next.js PWA)"]
        P[Hasta arayüzü<br/>mobil]
        D[Hekim dashboard<br/>masaüstü]
        SW[Service Worker<br/>web push]
    end

    subgraph API["FastAPI Backend"]
        AUTH[JWT Auth<br/>hasta/hekim/bakım veren]
        CRUD[REST: patients, medications,<br/>doses, measurements, alerts]
        COACH[/coach/chat/]
        GR[Guardrail<br/>girdi + çıktı]
        ADH[Uyum skoru servisi]
        SCH[APScheduler<br/>hatırlatma + haftalık özet]
    end

    subgraph AI["Yapay Zekâ"]
        LLM[Claude API<br/>tool use + JSON]
        RAG[RAG: education_contents<br/>pgvector]
    end

    DB[(PostgreSQL<br/>+ pgvector)]
    R[(Redis<br/>önbellek + limit)]
    FDA[openFDA<br/>Drug Label API]
    PUSH[Web Push servisi<br/>VAPID]

    P & D --> AUTH --> CRUD --> DB
    P --> COACH --> GR --> LLM
    LLM -- tool çağrısı --> ADH --> DB
    LLM -- tool çağrısı --> RAG --> DB
    CRUD -- ilaç etiketi --> FDA
    FDA -. önbellek .-> R
    COACH -. maliyet limiti .-> R
    SCH --> PUSH --> SW
```

## Monorepo yapısı

```
ilac-kocu/
├── backend/            FastAPI, SQLAlchemy, Alembic, testler
│   ├── app/
│   │   ├── routers/    REST uç noktaları
│   │   └── services/   uyum skoru, guardrail, openFDA, koç
│   ├── alembic/        migration'lar
│   └── tests/          pytest + guardrail senaryo seti
├── frontend/           Next.js PWA (M3)
├── docs/               M1 belgeleri, ER diyagramı
├── docker-compose.yml
└── .github/workflows/  CI
```

## Önemli tasarım kararları

1. **Uyum skoru deterministik.** LLM hesaplamaz; `get_adherence` tool'u ile okur. Test edilebilir ve tutarlı.
2. **Guardrail LLM'in dışında.** Girdi ve çıktı filtreleri saf Python fonksiyonları; birim testleriyle doğrulanır.
3. **Hasta kimliği tool'a sunucu bağlar.** Model `patient_id` parametresi veremez; başka hastaya erişim yapısal olarak imkânsız.
4. **LLM sağlayıcı soyutlaması.** `CoachProvider` arayüzü; testlerde sahte sağlayıcı, prod'da Claude.
