# Dayanıklılık, kuyruk işçisi ve yapay zekâ değerlendirmesi

Testler: `backend/tests/test_resilience.py`, `test_rag_postgres.py`, `test_coach_eval.py`.

## Devre kesici (`app/services/circuit_breaker.py`)

Dış servis art arda hata verirse bir süre hiç istek atılmaz; çağıran hemen yedeğe düşer (kullanıcı 30 sn'lik zaman aşımlarını beklemez).

| Devre | Nerede | Varsayılan | Açıkken |
|---|---|---|---|
| `llm` | `services/llm/client.create_message` (koç ve hekim anlatısı) | 60 sn'de 5 hata → 30 sn açık | `LlmError`: koç kural tabanlı yanıta, anlatı şablona düşer (`guardrail_action: llm_fallback`) |
| `openfda` | `services/openfda.fetch_label` | 60 sn'de 5 hata → 60 sn açık | `OpenFDAUnavailable`: `/drugs/label` 503 |

- Durumlar: kapalı → (eşik) → açık → (süre dolunca) yarı açık: **tek** deneme isteği; başarılıysa kapalı, değilse yeniden açık.
- Durum Redis'te (`cb:<ad>:failures|open_until|probe`): tüm API kopyaları ve işçi aynı devreyi görür; yarı açık denemeyi `SET NX` ile yalnızca bir süreç yapar. Redis yoksa süreç içi.
- Yalnızca ağ/sunucu hataları sayılır (bağlantı, zaman aşımı, 5xx, 429). Model yanıtının içeriği kullanılamazsa (ret, şema dışı) ya da openFDA 404 dönerse devre etkilenmez.
- Metrikler: `circuit_breaker_state{name}` (0/1/2), `circuit_breaker_rejected_total`, `circuit_breaker_opened_total`.
- Ayarlar: `LLM_BREAKER_FAILURE_THRESHOLD|WINDOW_SECONDS|OPEN_SECONDS`, `OPENFDA_BREAKER_*`.

## Koç işleri: yeniden deneme ve DLQ (`app/services/jobs.py`, `app/services/dlq.py`)

- LLM hatası zaten iş içinde kural tabanlı yanıta düşer. Yeniden deneme, **yedek yanıt da üretilemediğinde** (ör. veritabanı kesintisi) ya da işçi beklenmedik bir hatayla düştüğünde devreye girer.
- Deneme hakkı kaldıysa iş yeniden `queued` olur ve üstel beklemeyle (`COACH_JOB_RETRY_BASE_SECONDS` × 2^(deneme−1), en çok `COACH_JOB_RETRY_MAX_SECONDS`) Redis `coach:jobs:delayed` sıralı kümesine konur. İşçi her döngüde vadesi gelenleri atomik bir Lua betiğiyle kuyruğa taşır (birden çok işçi aynı işi iki kez taşımaz). Redis yoksa süreç içi yolda aynı görevde beklenerek denenir.
- `COACH_JOB_MAX_ATTEMPTS` (3) sonunda iş `failed` + `dead_lettered_at` ile DLQ'ya düşer; istemci "Koç yanıtı üretilemedi" görür. DLQ veritabanındadır (Redis yeniden başlasa da kaybolmaz).
- Gizlilik: DLQ'daki işin mesajı yalnızca yeniden deneme için `DLQ_RETENTION_DAYS` (7) gün tutulur; lider işçi her gece 03:30'da `dlq purge` ile siler. Listede içerik gösterilmez, `last_error` yalnızca hata türüdür.
- CLI: `python -m app.services.dlq list | retry <iş> | retry-all | purge`. Metrikler: `coach_dlq_size`, `coach_jobs_dead_lettered_total`.

## Lider işçi (`app/services/leader.py`, `app/worker.py`)

- Birden çok işçi açılabilir; koç kuyruğunu hepsi işler. Zamanlanmış işler yalnızca PostgreSQL oturum düzeyi advisory lock'u (`pg_try_advisory_lock`) tutan işçide çalışır, böylece çift hatırlatma ya da çift haftalık özet olmaz.
- Kilit ayrı bir AUTOCOMMIT bağlantıda tutulur ("idle in transaction" kalmaz). Lider ölür ya da bağlantısı koparsa PostgreSQL kilidi bırakır; bekleyen işçi `LEADER_RETRY_SECONDS` (15) içinde devralır. Lider de her turda bağlantısını yoklar, koptuysa zamanlayıcıyı durdurur.
- Elle denendi (PostgreSQL + Redis, `LEADER_RETRY_SECONDS=2`): A lider oldu, B bekledi; A'ya SIGTERM gönderilince B 2 sn içinde zamanlayıcıyı başlattı.
- Eski "ikinci işçide `REMINDERS_ENABLED=false`" kuralı artık gerekmez.

## Düzgün kapanma

SIGTERM/SIGINT: yeni iş alınmaz, elindeki koç işi bitirilir (iş ve yanıt aynı işlemde kaydedilir), çalışan zamanlanmış iş beklenir (`scheduler.shutdown(wait=True)`), liderlik kilidi bırakılır. Süre en çok BRPOP zaman aşımı (5 sn) + iş süresi; compose `stop_grace_period: 30s`. İkinci sinyal beklemeden çıkar. Kapanıştan sonra yarım kalmış iş olursa (`running`), sonraki işçinin açılış kurtarması onu yeniden kuyruğa alır.

## Zamanlanmış işler (`app/scheduler.py`)

| İş | Zaman (TIMEZONE) | Ne yapar |
|---|---|---|
| `dose_reminders` | her dakika | Vadesi gelen dozlar için hastaya web push (VAPID gerekir) |
| `weekly_summaries` | Pazartesi 07:00 (`WEEKLY_SUMMARY_CRON`) | Hekime atanmış her hasta için son 7 günün özetini `weekly_summaries`'e yazar (aynı dönem için tekrar üretmez); her hekime tek push: "N hastanızın haftalık özeti hazır" (hasta adı/sağlık verisi yok). Hekim push aboneliği hasta listesi sayfasından açılır. Kayıtlı özetler: `GET /v1/summaries/{id}/history` |
| `idempotency_purge` | saatte bir | TTL'i dolan Idempotency-Key kayıtları |
| `dlq_purge` | 03:30 | Saklama süresi dolan DLQ mesajları |

## Yapay zekâ değerlendirmesi

- **Koç kalite seti** (`backend/tests/coach_eval_scenarios.yaml`, değerlendirici `backend/scripts/coach_quality.py`): guardrail setinden ayrı olarak yanıtın işe yararlığını ölçer: hastanın sayılarına dayanma, doğru eğitim kaynağı, risk ve hekime yönlendirme, kapsamda kalma, suçlayıcı olmayan dil, uzunluk, çıktı guardrail'i. Kural tabanlı koç tüm ölçütleri geçmelidir (CI kapısı).
- **Model karşılaştırması**: `python -m scripts.eval_llm_models --live --models a,b` aynı seti her modelle koşturur ve [`docs/M4/llm-karsilastirma.md`](../M4/llm-karsilastirma.md)'yi yazar (geçme oranı, puan, gecikme, yedeğe düşme, maliyet). Gerçek modellerle henüz koşturulmadı: ücretli API çağrısı olduğu için kullanıcı onayı bekliyor.
- **pgvector**: `test_rag_postgres.py` RAG'in PostgreSQL yolunu (`<=>` kosinüs, etiket filtresi, reddedilen içeriğin elenmesi) geçici bir şemada sınar.
