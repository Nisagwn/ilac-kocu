"""Koç kalite değerlendirme seti (tests/coach_eval_scenarios.yaml) — deterministik kısım, CI'da koşar.

Her senaryo: sentetik veri kurulur -> araç çıktıları (get_adherence / get_measurements) beklenen sayıları verir mi
-> tam koç hattı (girdi guardrail + kural tabanlı koç + çıktı guardrail) yanıtı veriyi doğru yansıtıyor mu,
kaynaklar dolu ve doğrulanmış mı, doz önermiyor mu, risk_level / escalate uygun mu.
Gerçek LLM ile aynı set: scripts/eval_llm_models.py (CI'da çalışmaz). Ağ çağrısı YOK.
"""

import json

import pytest

from app.models import EducationContent
from app.seed import seed_education
from app.services.coach import RuleBasedCoach, handle_message
from app.services.llm.coach_agent import ClaudeCoachProvider
from scripts.coach_eval_data import build_patient, check_reply, check_tools, load_scenarios
from tests.test_coach_llm import FakeClient, final, llm_settings, response, tool_use

SCENARIOS = load_scenarios()


def test_scenario_set_integrity():
    ids = [s["id"] for s in SCENARIOS]
    assert 20 <= len(SCENARIOS) <= 30
    assert len(set(ids)) == len(ids)
    for s in SCENARIOS:
        assert s["message"].strip() and isinstance(s.get("expect"), dict), s["id"]
    kinds = {s["expect"].get("sources") for s in SCENARIOS}
    assert {"who", "education", "none"} <= kinds
    assert {s["expect"].get("guardrail_action") for s in SCENARIOS} >= {"emergency", "self_harm", "dose_request"}


@pytest.fixture()
def seeded(db):
    seed_education(db)
    return db


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_rule_based_coach_scenario(seeded, sc):
    patient = build_patient(seeded, sc)
    fails = check_tools(seeded, patient.id, sc["expect"])
    reply = handle_message(seeded, patient, sc["message"], provider=RuleBasedCoach())
    fails += check_reply(seeded, reply, sc["expect"], rule_based=True)
    assert not fails, f"{sc['id']} ({sc['description']}): " + "; ".join(fails) + f"\nYanıt: {reply.message}"


def _scripted_llm_for(sc, seeded):
    """Gerçekçi bir tool-use akışı: veriyi araçlarla okur, araç sonuçlarındaki sayılarla yanıt yazar,
    biri uydurma olmak üzere kaynak id'leri döndürür."""
    state = {}

    def after_tools(kwargs):
        results = [b for b in kwargs["messages"][-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        adh = json.loads(results[0]["content"])
        edu = json.loads(results[1]["content"])["results"]
        state["edu_ids"] = [r["id"] for r in edu]
        msg = (f"Son 7 günde {adh['planned']} dozun {adh['taken']} tanesini almışsın (%{adh['percent']}). "
               f"{adh['missed']} doz kaçmış; ilacını akşam yemeğine bağlamak işe yarayabilir.")
        return final(msg, risk="low", source_ids=state["edu_ids"][:1] + [99999])

    return FakeClient([
        response([tool_use("t1", "get_adherence", {"days": 7}),
                  tool_use("t2", "search_education", {"query": sc["message"]})], stop="tool_use"),
        after_tools,
    ])


def test_llm_path_reflects_tool_data_and_grounds_sources(seeded):
    sc = next(s for s in SCENARIOS if s["id"] == "C06")
    patient = build_patient(seeded, sc)
    client = _scripted_llm_for(sc, seeded)
    provider = ClaudeCoachProvider(client=client, settings=llm_settings())
    reply = handle_message(seeded, patient, sc["message"], provider=provider)
    assert reply.guardrail_action is None
    assert not check_reply(seeded, reply, sc["expect"], rule_based=False)
    titles = [s.title for s in reply.sources]
    assert len(titles) == 1 and seeded.query(EducationContent).filter_by(title=titles[0]).count() == 1
    assert "3 doz" in reply.message and "%79" in reply.message


def test_check_reply_flags_dose_advice_and_wrong_numbers(seeded):
    from app.services.coach import CoachReply

    sc = next(s for s in SCENARIOS if s["id"] == "C01")
    bad = CoachReply(message="Dozunu iki katına çıkar, 2 tablet al.", sources=[])
    fails = check_reply(seeded, bad, sc["expect"], rule_based=False)
    assert any("guardrail" in f for f in fails) and any("sayı" in f for f in fails)
    assert any("WHO" in f for f in fails)
