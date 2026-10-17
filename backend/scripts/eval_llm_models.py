"""LLM model karşılaştırması: koç kalite seti (tests/coach_eval_scenarios.yaml) her modelle koşturulur.

Her model için: senaryo geçme oranı, ortalama ölçüt puanı, gecikme (p50/p95), kural tabanlı yedeğe düşme
sayısı ve LlmUsage kaydından toplam maliyet. Sonuç Markdown rapor olarak yazılır
(varsayılan docs/M4/llm-karsilastirma.md). Kural tabanlı koç her raporda taban çizgisidir.

Kullanım (backend/ dizininde):
    python -m scripts.eval_llm_models                 # yalnızca kural tabanlı taban çizgisi (ağ YOK)
    python -m scripts.eval_llm_models --live --models anthropic/claude-sonnet-5.5,anthropic/claude-haiku-5.5

--live GERÇEK API çağrısı yapar ve ücretlidir (model başına senaryo sayısı kadar koç isteği, her biri
tool-use döngüsüyle birkaç çağrı). OPENROUTER_API_KEY ya da ANTHROPIC_API_KEY gerekir. Senaryolar
sentetik hastalarla bellek içi SQLite'ta çalışır; gerçek hasta verisi gönderilmez.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts import coach_quality  # noqa: E402  (ortam değişkenleri burada ayarlanır)

DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "M4" / "llm-karsilastirma.md"

HEADER = """# LLM model karşılaştırması

Bu dosya `backend/scripts/eval_llm_models.py` ile üretilir; elle düzenlemeyin.

## Yöntem

- Senaryolar: `backend/tests/coach_eval_scenarios.yaml` ({n} senaryo; kategoriler: {cats}). Her senaryo yeni bir
  sentetik hasta kurar (son 7 gün doz kaydı, isteğe bağlı ölçüm ve eğitim içeriği) ve mesajı tam koç hattından
  geçirir: girdi guardrail'i → model (tool-use + structured output) → çıktı guardrail'i.
- Ölçütler (senaryo başına): boş olmama, çıktı guardrail'inden geçme, veriye dayalılık (hastanın sayıları),
  beklenen içerik, yasaklı ifade yokluğu, kaynak sayısı ve doğru kaynak, risk düzeyi, hekime yönlendirme,
  guardrail eylemi, uzunluk. Senaryo ancak tüm ölçütleri geçerse "geçti" sayılır; "puan" geçen ölçüt oranıdır.
- Gecikme: tam hat süresi (model çağrıları + araçlar). Maliyet: `llm_usage` kayıtlarından (USD).
- Kural tabanlı koç taban çizgisidir ve CI'da her değişiklikte aynı setle koşar (`tests/test_coach_eval.py`).

"""


def provider_factory(model: str):
    from app.config import Settings
    from app.services.llm.coach_agent import ClaudeCoachProvider

    settings = Settings(coach_provider="auto", llm_model=model)
    if not settings.llm_enabled:
        raise SystemExit("LLM anahtarı yok: OPENROUTER_API_KEY ya da ANTHROPIC_API_KEY tanımlayın")
    return lambda: ClaudeCoachProvider(settings=settings)


def render(rows: list[tuple[str, dict, list]], n: int, cats: str, live: bool) -> str:
    out = [HEADER.format(n=n, cats=cats)]
    out.append(f"## Sonuçlar ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)\n")
    if not live:
        out.append("> Gerçek modellerle henüz koşturulmadı (ücretli API; kullanıcı onayıyla `--live`). "
                   "Aşağıda yalnızca kural tabanlı taban çizgisi var.\n")
    out.append("| Model | Geçen senaryo | Ortalama puan | p50 (ms) | p95 (ms) | Yedeğe düşme | Maliyet (USD) |")
    out.append("|---|---|---|---|---|---|---|")
    for name, s, _ in rows:
        out.append(f"| {name} | {s['passed']}/{s['total']} (%{round(s['pass_rate'] * 100)}) | {s['mean_score']:.3f} | "
                   f"{s['p50_ms']:.0f} | {s['p95_ms']:.0f} | {s['fallbacks']} | {s['cost_usd']:.4f} |")
    out.append("\n## Kalan ölçütler\n")
    for name, _, results in rows:
        failed = [(r.id, [k for k, ok in r.checks.items() if not ok]) for r in results if not r.passed]
        out.append(f"- **{name}**: " + ("hepsi geçti" if not failed else
                                       "; ".join(f"{i}: {', '.join(c)}" for i, c in failed)))
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="", help="virgülle ayrılmış model kimlikleri (LLM_MODEL biçiminde)")
    ap.add_argument("--live", action="store_true", help="gerçek API çağrısı yap (ücretli)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    os.environ["COACH_PROVIDER"] = "auto" if args.live else "rule"
    scenarios = coach_quality.load_scenarios()
    cats = ", ".join(sorted({s["category"] for s in scenarios}))
    base = coach_quality.run(scenarios)
    rows = [("kural tabanlı (taban)", coach_quality.summarize(base), base)]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if models and not args.live:
        print("Model verildi ama --live yok: gerçek API çağrısı yapılmadı.", file=sys.stderr)
    if args.live:
        for model in models:
            res = coach_quality.run(scenarios, provider_factory(model))
            rows.append((model, coach_quality.summarize(res), res))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, len(scenarios), cats, args.live and bool(models)), encoding="utf-8")
    print(f"Rapor yazıldı: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
