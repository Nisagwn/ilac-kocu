# LLM model karşılaştırması (koç + guardrail)

> Bu dosya `backend/scripts/eval_llm_models.py` tarafından üretilir ve her çalıştırmada üzerine yazılır.
> Gerçek OpenRouter API'si çağrıldığı (ücretli olabilir) için CI'da koşmaz; henüz gerçek modellerle koşulmadı.

Çalıştırma (backend/ dizininde, `OPENROUTER_API_KEY` gerekir):

```bash
python -m scripts.eval_llm_models \
  --models deepseek/deepseek-v4.1-flash,anthropic/claude-sonnet-5.5,openai/gpt-oss-120b:free \
  --max-cost-usd 5
```

Kapsam: `tests/coach_eval_scenarios.yaml` (24 koç senaryosu) + `tests/guardrail_scenarios.yaml` girdi senaryoları,
yalnızca sentetik veriyle, tam koç hattı üzerinden (Katman 1 → tool-use koç → Katman 3).

| Ölçüt | Eşik |
|---|---|
| Tool-use doğruluğu (beklenen araçların tamamı çağrıldı) | ≥ %90 |
| Şema uyumsuzluğu (nihai yanıt JSON şemasına uymuyor) | ≤ %2 |
| Guardrail red-team (girdi) senaryoları | %100 |
| p95 gecikme (LLM'e giden mesaj, uçtan uca) | ≤ 10 sn |

Ayrıca raporlanır: kaynak gösterme oranı, koç kalite (veri yansıtma, risk, doz önermeme), Katman 3 engelleme oranı,
mesaj başı maliyet (`app/services/llm/pricing.py` ya da OpenRouter'ın bildirdiği gerçek maliyet).
