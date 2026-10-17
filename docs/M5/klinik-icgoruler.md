# Klinik içgörüler ve eğitim içeriği onayı

## Klinik özet (`GET /v1/patients/{id}/insights?days=30`)

Kod: `backend/app/services/clinical_insights.py`. Testler: `backend/tests/test_clinical_insights.py`. Arayüz: hekim/bakım veren hasta detayında "Klinik özet" kartı (`frontend/src/components/clinician/clinical-summary.tsx`), hasta listesinde 7 günlük uyumun yanında eğilim oku.

Tamamen deterministiktir (LLM yok) ve yalnızca kayıtlı veriyi özetler; **teşhis ya da tedavi önerisi içermez**. Erişim kuralı hasta verisinin geri kalanıyla aynıdır (hasta kendisi, atanmış hekim, bağlı bakım veren; yetkisiz → 404) ve hekim/bakım veren erişimi KVKK erişim kaydına yazılır.

| Bölüm | Tanım |
|---|---|
| Uyum eğilimi | Son 7 gün ile önceki 7 gün (7-13 gün önce) uyum skoru. Fark ≥ +10 puan "improving", ≤ −10 puan "worsening", arası "stable"; iki pencereden biri boşsa `null`. Hasta listesinde (`adherence_trend`, `adherence_prev_7d`) de döner. |
| Hedef aralıkta kalma | Kan şekeri 70-180 mg/dL; tansiyon sistolik 90-129 ve diyastolik <80 mmHg. Grafiklerdeki hedef bantlarıyla aynıdır; uyarı (alert) eşikleri ayrıdır (`services/thresholds.py`). Düşük/yüksek ölçüm sayıları ayrıca verilir. |
| Doz atlama nedenleri | Hastanın "Almadım" derken yazdığı serbest metin anahtar kelimeyle gruplanır: unutma, yan etki / rahatsızlık, ilaç bitti, rutin dışı gün, gerek duymama, öğün atlama / oruç, diğer, belirtilmemiş. Türkçe karakter ve büyük/küçük harf duyarsızdır. |
| Ölçüm sıklığı | Tür başına sayı, haftalık ortalama, ölçüm yapılan gün sayısı, son ölçümden beri geçen gün (pencere dışındaki son ölçüm de dikkate alınır). |
| Kaçırılan doz saatleri | Son 14 günde gün dilimine (sabah/öğle/akşam/gece) göre planlanan ve kaçırılan doz. |
| Vurgular (`highlights`) | Yukarıdakilerden türetilen olgusal cümleler: uyum ±10 puan değiştiyse, kan şekeri hedefte kalma <%70 ise, düşük kan şekeri ölçümü varsa, tansiyon hedefte <%50 ise, son ölçüm ≥3 gün önceyse, en sık belirtilen atlama nedeni, kaçırılan dozların ≥%40'ı aynı gün dilimindeyse. |

Sınırlılık: anahtar kelime gruplaması basittir; yazım hatası ya da beklenmedik ifade "diğer"e düşer. Eşikler genel kabul görmüş değerlerdir ve danışman hekimce gözden geçirilmelidir.

## Eğitim içeriği onayı (`/hekim/egitim`)

Kod: `backend/app/routers/education.py`, `backend/app/models.py` (`set_review`), migration `0009_education_review`. Testler: `backend/tests/test_education_review.py`.

- Her eğitim içeriği (kürate ve openFDA) `review_status` taşır: `pending` (varsayılan), `approved`, `rejected`. Onaylayan/reddeden hekim, zaman ve not saklanır; geriye dönük `reviewed` alanı = `approved`.
- Hekim `POST /v1/education/{id}/approve`, `/reject` (not zorunlu) ve `/reset` (kararı geri al) ile karar verir. Hasta ve bakım veren karar veremez (403).
- **Reddedilen içerik RAG aramasına hiç girmez** (PostgreSQL/pgvector ve Python yolu); koç onu kaynak gösteremez. Onay bekleyen içerik kullanılmaya devam eder: sistemin ilk kurulumda boş kalmaması için bilinçli tercih; onay ekranı hekimin dikkatini bekleyenlere çeker.
- İçerik metni değişirse karar sıfırlanır: kürate YAML'da metin değişince ya da openFDA etiketi farklı metinle yeniden eklenince içerik yeniden `pending` olur. Metin değişmeden yeniden seed/ingest hekim kararını ezmez.
- `POST /education/ingest-drug` bakım verene kapalıdır (403) ve `/drugs/label` bakım veren isteğinde eğitim içeriğine yazmaz: bakım veren rolü salt okunurdur.
