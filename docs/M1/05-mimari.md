# Mimari

> İlk sürüm M1'de taslak olarak yazıldı; bu sürüm M5 itibarıyla **gerçekleşen sistemi** yansıtır.
> Tasarım kararları (aşağıda) M1'den bu yana değişmedi; yalnızca bileşenler somutlaştı.

## 1. Bileşen diyagramı

```mermaid
flowchart LR
    subgraph Browser["Tarayıcı / ana ekrana eklenmiş PWA"]
        UI_P["Hasta ekranları<br/>/bugun /olcum /koc /ilaclar /ozet"]
        UI_D["Hekim dashboard'u<br/>/hekim /hekim/hasta/:id /hekim/uyarilar"]
        UI_C["Bakım veren (salt okunur)<br/>/yakin /yakin/hasta/:id"]
        SW["Service Worker (sw.js)<br/>push + offline kabuk<br/>/api/* ASLA önbelleğe alınmaz"]
    end

    subgraph Next["Next.js 15 sunucusu (web :3000)"]
        MW["middleware.ts<br/>rol alanı yönlendirmesi (yalnızca UX)"]
        LOGIN["/api/auth/login · logout<br/>JWT → httpOnly + SameSite=Lax çerez"]
        PROXY["/api/proxy/[...path]<br/>çerez → Authorization: Bearer"]
    end

    subgraph API["FastAPI (api :8000) — OpenAPI /docs"]
        DEPS["deps.py<br/>JWT doğrulama · require_roles<br/>get_patient_for_read/write → 404"]
        R_CRUD["routers: auth, patients, medications,<br/>doses, measurements, alerts, summaries"]
        R_COACH["routers/coach.py<br/>POST /coach/chat"]
        R_DRUG["routers/drugs.py<br/>GET /drugs/label"]
        R_PUSH["routers/push.py<br/>VAPID abonelik"]
        TH["thresholds.py<br/>eşik dışı ölçüm → alert"]
        ADH["adherence.py<br/>deterministik uyum skoru"]
        GR["guardrails.py<br/>Katman 1 girdi · Katman 3 çıktı"]
        COACH["coach.py orkestrasyon<br/>Claude tool-use döngüsü<br/>↘ yedek: RuleBasedCoach"]
        TOOLS["Tool'lar (patient_id sunucu bağlar)<br/>get_adherence · get_measurements<br/>get_medications · search_education"]
        BUDGET["Maliyet kontrolü<br/>hasta/gün istek+token · aylık USD<br/>llm_usage tablosu"]
        RAG["services/rag<br/>embedding (yerel deterministik /<br/>isteğe bağlı Voyage) + pgvector"]
        SUM["summary.py<br/>haftalık istatistik + LLM anlatı<br/>↘ yedek: deterministik metin"]
        FDA_S["openfda.py<br/>TR ticari ad → etken madde<br/>etiket → eğitim içeriği"]
        SCH["APScheduler (reminders.py)<br/>dakikada bir, 15 dk pencere"]
        PUSH_S["push.py (pywebpush)"]
    end

    DB[("PostgreSQL 16 + pgvector<br/>Alembic migration'ları")]
    REDIS[("Redis<br/>openFDA önbelleği")]
    CLAUDE["Claude API<br/>Messages + tool use"]
    VOY["Voyage AI<br/>(isteğe bağlı embedding)"]
    OFDA["openFDA<br/>Drug Label API"]
    WPS["Tarayıcı push servisi<br/>(FCM / Mozilla / APNs)"]

    UI_P & UI_D & UI_C -->|fetch /api/proxy/*| PROXY
    UI_P -->|giriş| LOGIN
    LOGIN -->|POST /auth/login| R_CRUD
    PROXY --> DEPS
    DEPS --> R_CRUD & R_COACH & R_DRUG & R_PUSH
    R_CRUD --> TH & ADH & SUM
    R_CRUD --> DB
    R_COACH --> GR --> COACH
    COACH --> BUDGET --> DB
    COACH -->|"yalnızca takma kimlik + sayısal özet"| CLAUDE
    CLAUDE -->|tool_use| TOOLS
    TOOLS --> ADH & RAG
    TOOLS --> DB
    RAG --> DB
    RAG -.-> VOY
    COACH -->|"yanıt → Katman 3 + kaynak doğrulama"| GR
    SUM -.->|anlatı| CLAUDE
    R_DRUG --> FDA_S --> OFDA
    FDA_S -.önbellek.-> REDIS
    FDA_S -->|etiket özetleri| DB
    SCH --> PUSH_S --> WPS --> SW
```

> Not: Diyagramdaki LLM, RAG, maliyet kontrolü ve LLM anlatısı M4 kapsamındadır.
> Maliyet sayaçları Redis'te değil, yalnızca PostgreSQL'deki `llm_usage` tablosunda tutulur (migration `0004_llm_usage`); Redis yalnızca openFDA etiket önbelleği (24 saat) içindir ve `REDIS_URL` tanımlıysa kullanılır.

## 2. Koç isteğinin yaşam döngüsü

```mermaid
sequenceDiagram
    autonumber
    participant H as Hasta (PWA)
    participant N as Next.js proxy
    participant A as FastAPI /coach/chat
    participant G as Guardrail (deterministik)
    participant B as Bütçe / limit
    participant L as Claude API
    participant T as Tool'lar
    participant D as PostgreSQL

    H->>N: POST /api/proxy/coach/chat (httpOnly çerez)
    N->>A: Authorization: Bearer JWT
    A->>A: rol = patient? hasta kaydını JWT'den bul
    A->>G: Katman 1: check_input(mesaj)
    alt acil / kendine zarar
        G-->>A: sabit 112 yanıtı
        A->>D: kritik alert (hekim listesinde en üstte)
    else doz / ilaç değişikliği
        G-->>A: sabit "hekiminize danışın" yanıtı + info alert
    else temiz
        A->>B: günlük istek/token + aylık USD limiti
        alt limit aşıldı veya API anahtarı yok / hata
            B-->>A: RuleBasedCoach (yedek)
        else
            A->>L: system prompt + tool tanımları + mesaj (ad/e-posta yok)
            loop tool_use (üst sınırlı tur)
                L->>T: get_adherence / get_measurements / get_medications / search_education
                T->>D: sorgu (patient_id sunucuda bağlı)
                T-->>L: sayısal özet / eğitim parçaları
            end
            L-->>A: structured output (message, risk_level, escalate_to_clinician, sources)
            A->>A: sources ⊆ bu turda search_education'ın döndürdüğü kayıtlar?
            A->>G: Katman 3: check_output(message)
        end
    end
    A->>D: coach_messages + llm_usage (+ alert)
    A-->>H: yanıt + kaynaklar
```

## 3. Dağıtım (Docker Compose)

| Servis | İmaj | Port | Not |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | 5432 | `pgdata` volume, healthcheck |
| `redis` | `redis:7-alpine` | 6379 | önbellek |
| `api` | `./backend` (python:3.12-slim, root olmayan kullanıcı) | 8000 | açılışta `alembic upgrade head`; APScheduler aynı süreçte |
| `web` | `./frontend` (Next.js `standalone`, root olmayan kullanıcı) | 3000 | `API_URL=http://api:8000` — backend adresi tarayıcıya açılmaz |

## 4. Monorepo yapısı

```
ilac-kocu/
├── backend/
│   ├── app/
│   │   ├── routers/      auth, patients, medications, doses, measurements,
│   │   │                 alerts, summaries, coach, drugs, push
│   │   ├── services/     adherence, guardrails, coach, thresholds, summary,
│   │   │                 openfda, push, reminders, rag/
│   │   ├── deps.py       kimlik doğrulama + rol bazlı erişim (404 politikası)
│   │   ├── models.py     SQLAlchemy modelleri
│   │   └── seed.py       sentetik demo verisi
│   ├── alembic/          sürümlü migration'lar
│   └── tests/            pytest + guardrail_scenarios.yaml (red-team seti)
├── frontend/
│   ├── src/app/          (hasta)/…, hekim/…, yakin/…, api/auth, api/proxy
│   ├── src/middleware.ts rol alanı yönlendirmesi
│   └── public/           manifest, sw.js, offline.html, ikonlar
├── docs/                 M1, M4, M5 belgeleri, ER diyagramı
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## 5. Önemli tasarım kararları

1. **Uyum skoru deterministik.** LLM hesaplamaz; `get_adherence` tool'u ile okur. Test edilebilir ve tutarlı (`backend/app/services/adherence.py`, `tests/test_adherence.py`).
2. **Guardrail LLM'in dışında.** Girdi ve çıktı filtreleri saf Python fonksiyonları; red-team setiyle CI'da doğrulanır. Acil durum yolu LLM'e hiç uğramaz; API anahtarı olmasa da çalışır.
3. **Hasta kimliğini tool'a sunucu bağlar.** Tool şemalarında `patient_id` parametresi yoktur; model başka hastaya erişemez.
4. **Kaynaklar sunucuda doğrulanır (grounding).** Modelin döndürdüğü `sources`, aynı turda `search_education` tool'unun gerçekten döndürdüğü kayıtlarla eşleşmezse atılır; uydurma kaynak hastaya ulaşmaz.
5. **Her LLM yolunun deterministik yedeği var.** API anahtarı yok, bütçe doldu ya da API hatası → kural tabanlı koç; haftalık anlatı üretilemezse → istatistikten üretilen sabit şablon.
6. **Token tarayıcı JavaScript'ine verilmez.** JWT yalnızca Next.js sunucusundaki httpOnly çerezde durur; tarayıcı yalnızca aynı kökenli `/api/proxy/*` ile konuşur, CORS ve token sızıntısı yüzeyi kalkar.
7. **Service worker sağlık verisini saklamaz.** `/api/*` yanıtları ve kişiye özel HTML önbelleğe alınmaz; yalnızca kişisel veri içermeyen kabuk dosyaları saklanır.
8. **Tek süreç zamanlayıcı.** APScheduler API süreci içinde koşar; proje ölçeğinde yeterli, yatay ölçeklemede ayrı worker'a taşınmalıdır (bkz. final rapor, sınırlılıklar).

## 6. Gerçekleşen sistem

<table>
<tr>
<td align="center"><img src="../gorseller/02-hasta-bugun.png" width="260" alt="Bugün (hasta, PWA)"><br><sub>Bugün (hasta, PWA)</sub></td>
<td align="center"><img src="../gorseller/06-hasta-koc.png" width="260" alt="Koç (hasta, PWA)"><br><sub>Koç (hasta, PWA)</sub></td>
</tr>
</table>

![Hekim hasta listesi](../gorseller/07-hekim-hasta-listesi.png)

*Hekim dashboard'u: triyaj listesi.*

![Swagger](../gorseller/11-swagger.png)

*FastAPI OpenAPI arayüzü (`/docs`).*
