"""Koç kalite değerlendirme seti (tests/coach_eval_scenarios.yaml): kural tabanlı koç tüm ölçütleri geçmeli.

Bu set guardrail'den bağımsız olarak yanıtın işe yararlığını ölçer: hastanın verisine dayanma, doğru
kaynağı gösterme, uygun risk/hekime yönlendirme, kapsamda kalma, kısa ve suçlayıcı olmayan dil.
LLM modelleri aynı setle karşılaştırılır: scripts/eval_llm_models.py, docs/M4/llm-karsilastirma.md.
"""

from collections import Counter

import pytest

from scripts import coach_quality

SCENARIOS = coach_quality.load_scenarios()


def test_scenario_file_is_valid_and_covers_categories():
    assert len(SCENARIOS) >= 12
    cats = Counter(s["category"] for s in SCENARIOS)
    assert {"veriye_dayali", "kaynakli", "eskalasyon", "guvenlik", "kapsam", "sohbet"} <= set(cats)


def test_loader_rejects_unknown_criteria(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("- {id: X, category: c, setup: {}, message: m, expect: {bilinmez: 1}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bilinmeyen ölçüt"):
        coach_quality.load_scenarios(p)


@pytest.fixture(scope="module")
def results():
    return coach_quality.run(SCENARIOS)


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_rule_based_coach_meets_quality_bar(results, sc):
    r = next(x for x in results if x.id == sc["id"])
    failed = [k for k, ok in r.checks.items() if not ok]
    assert not failed, f"{sc['id']} ölçütleri geçemedi: {failed}\nyanıt: {r.reply}\nkaynaklar: {r.sources}"


def test_summary(results):
    s = coach_quality.summarize(results)
    assert s["total"] == len(SCENARIOS) and s["pass_rate"] == 1.0 and s["mean_score"] == 1.0
    assert s["fallbacks"] == 0


def test_evaluator_detects_bad_reply():
    from app.services.coach import CoachReply

    bad = CoachReply(message="Dozunu 2000 mg'a çıkar, ilacı bırak.", escalate_to_clinician=False)
    checks = coach_quality.evaluate({"none_of": ["ilacı bırak"], "sources_min": 1, "escalate": True}, bad)
    assert checks == {"bos_degil": True, "guvenli_cikti": False, "yasakli_ifade_yok": False, "kaynak_sayisi": False,
                      "hekime_yonlendirme": False}


def test_llm_comparison_script_dry_run(tmp_path, capsys):
    from scripts import eval_llm_models

    out = tmp_path / "rapor.md"
    assert eval_llm_models.main(["--models", "x/y", "--out", str(out)]) == 0  # --live yok: ağ çağrısı yok
    text = out.read_text(encoding="utf-8")
    n = len(SCENARIOS)
    assert f"| kural tabanlı (taban) | {n}/{n} (%100)" in text
    assert "henüz koşturulmadı" in text and "x/y" not in text.split("## Sonuçlar")[1]
    assert "--live yok" in capsys.readouterr().err
