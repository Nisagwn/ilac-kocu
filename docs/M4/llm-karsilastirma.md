# LLM model karşılaştırması

Bu dosya `backend/scripts/eval_llm_models.py` ile üretilir; elle düzenlemeyin.

## Yöntem

- Senaryolar: `backend/tests/coach_eval_scenarios.yaml` (14 senaryo; kategoriler: eskalasyon, guvenlik, kapsam, kaynakli, sohbet, veriye_dayali). Her senaryo yeni bir
  sentetik hasta kurar (son 7 gün doz kaydı, isteğe bağlı ölçüm ve eğitim içeriği) ve mesajı tam koç hattından
  geçirir: girdi guardrail'i → model (tool-use + structured output) → çıktı guardrail'i.
- Ölçütler (senaryo başına): boş olmama, çıktı guardrail'inden geçme, veriye dayalılık (hastanın sayıları),
  beklenen içerik, yasaklı ifade yokluğu, kaynak sayısı ve doğru kaynak, risk düzeyi, hekime yönlendirme,
  guardrail eylemi, uzunluk. Senaryo ancak tüm ölçütleri geçerse "geçti" sayılır; "puan" geçen ölçüt oranıdır.
- Gecikme: tam hat süresi (model çağrıları + araçlar). Maliyet: `llm_usage` kayıtlarından (USD).
- Kural tabanlı koç taban çizgisidir ve CI'da her değişiklikte aynı setle koşar (`tests/test_coach_eval.py`).


## Sonuçlar (2026-10-17 13:24 UTC)

> Gerçek modellerle henüz koşturulmadı (ücretli API; kullanıcı onayıyla `--live`). Aşağıda yalnızca kural tabanlı taban çizgisi var.

| Model | Geçen senaryo | Ortalama puan | p50 (ms) | p95 (ms) | Yedeğe düşme | Maliyet (USD) |
|---|---|---|---|---|---|---|
| kural tabanlı (taban) | 14/14 (%100) | 1.000 | 9 | 36 | 0 | 0.0000 |

## Kalan ölçütler

- **kural tabanlı (taban)**: hepsi geçti
