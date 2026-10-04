"""Claude tool-use koç testleri — sahte Anthropic istemcisiyle (ağ çağrısı YOK)."""

import json
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.models import (
    CoachMessage,
    DoseLog,
    DoseStatus,
    EducationContent,
    LlmUsage,
    Medication,
    MedicationSchedule,
    Patient,
    Sender,
)
from app.services.coach import RuleBasedCoach, get_adherence, get_coach_provider, handle_message
from app.services.llm.client import SERVER_FALLBACK_BETA
from app.services.llm.coach_agent import BUDGET_NOTE, ClaudeCoachProvider
from app.services.llm.pricing import FALLBACK_PRICE, cost_usd
from tests.conftest import register


# ---------------- Sahte istemci ----------------
def tool_use(tid, name, tool_input):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=tool_input)


def text(t):
    return SimpleNamespace(type="text", text=t)


def response(content, stop="end_turn", inp=1000, out=200, cache_read=0, cache_write=0, model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
                            cache_creation_input_tokens=cache_write)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, model=model)


def final(message="Harika gidiyorsun!", risk="low", escalate=False, source_ids=(), **kw):
    payload = {"message": message, "risk_level": risk, "escalate_to_clinician": escalate,
               "source_ids": list(source_ids)}
    return response([SimpleNamespace(type="thinking", thinking=""), text(json.dumps(payload, ensure_ascii=False))],
                    **kw)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        # mesaj listesi döngüde büyüdüğü için çağrı anındaki kopyası saklanır
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(kwargs) if callable(item) else item


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self):
        return self.messages.calls


def llm_settings(**kw) -> Settings:
    return Settings(anthropic_api_key="sk-test", coach_provider="auto", **kw)


def provider_with(script, **settings_kw):
    client = FakeClient(script)
    return ClaudeCoachProvider(client=client, settings=llm_settings(**settings_kw)), client


def tool_results(call: dict) -> list[dict]:
    last = call["messages"][-1]
    return [b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


# ---------------- Veri hazırlığı ----------------
def add_plan(db, patient_id, name="Metformin", hour=8, taken_days=5):
    med = Medication(patient_id=patient_id, name=name, active_ingredient="metformin", dose_text="1000 mg")
    db.add(med)
    db.flush()
    sched = MedicationSchedule(medication_id=med.id, time_of_day=time(hour, 0),
                               start_date=datetime.now(UTC).date() - timedelta(days=20))
    db.add(sched)
    db.flush()
    today = datetime.now(UTC).date()
    for i in range(1, taken_days + 1):
        db.add(DoseLog(patient_id=patient_id, schedule_id=sched.id, scheduled_date=today - timedelta(days=i),
                       status=DoseStatus.taken))
    db.commit()
    return med


@pytest.fixture()
def pat(db, patient):
    user, h = patient
    return db.get(Patient, user["patient_id"]), h


@pytest.fixture()
def edu(db):
    row = EducationContent(title="İlacı rutine bağlamak", body="İlacı kahvaltıya bağlamak hatırlamayı kolaylaştırır.",
                           source_name="Sağlık Bakanlığı", source_url="https://example.org/rutin", tags="uyum")
    db.add(row)
    db.commit()
    return row


# ---------------- Testler ----------------
def test_tool_loop_executes_tools_and_returns_structured_reply(db, pat, edu, monkeypatch):
    p, _ = pat
    add_plan(db, p.id)
    from app.services import rag

    monkeypatch.setattr(rag, "search_education", lambda db, q, k=3, tags=None: [
        rag.EducationHit(edu.id, edu.title, edu.body, edu.source_name, edu.source_url, 0.9)])

    provider, client = provider_with([
        response([tool_use("t1", "get_adherence", {"days": 7}),
                  tool_use("t2", "search_education", {"query": "ilaç hatırlatma"})], stop="tool_use"),
        final("Son 7 günde 5 dozun 5'ini almışsın, süper!", source_ids=[edu.id, 999, 0]),
    ])
    reply = handle_message(db, p, "Bu hafta nasılım?", provider=provider)

    assert reply.message.startswith("Son 7 günde") and reply.guardrail_action is None
    # 999 araçlardan gelmedi -> atıldı; WHO (0) kabul
    assert [s.title for s in reply.sources] == [edu.title, "WHO — Adherence to long-term therapies: evidence for action (2003)"]
    assert len(client.calls) == 2

    first = client.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in first["tools"]] == ["get_adherence", "get_measurements", "get_medications",
                                                   "search_education"]
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert first["output_config"]["effort"] == "low"
    assert first["model"] == "claude-opus-5-5" and first["max_tokens"] == 2048
    assert first["fallbacks"] == "default" and first["betas"] == [SERVER_FALLBACK_BETA]

    results = tool_results(client.calls[1])
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and not any(r.get("is_error") for r in results)
    adh = json.loads(results[0]["content"])
    assert adh["planned"] == get_adherence(db, p.id, 7).planned and adh["taken"] == 5
    assert adh["by_medication"][0]["medication"] == "Metformin"
    assert json.loads(results[1]["content"])["results"][0]["id"] == edu.id

    saved = db.query(CoachMessage).filter_by(sender=Sender.coach).one()
    assert saved.sources[0]["title"] == edu.title


def test_no_personal_data_sent_to_llm(db, pat):
    p, _ = pat
    add_plan(db, p.id)
    provider, client = provider_with([
        response([tool_use("t1", "get_medications", {}), tool_use("t2", "get_measurements",
                                                               {"days": 30, "type": "all"})], stop="tool_use"),
        final(),
    ])
    handle_message(db, p, "ilaçlarım neler?", provider=provider)
    dumped = json.dumps(client.calls, default=str, ensure_ascii=False)
    assert "ayse@example.com" not in dumped and "Ayse" not in dumped
    assert "Metformin" in dumped  # ilaç adı bağlam olarak gider


def test_ungrounded_sources_are_stripped(db, pat, edu):
    p, _ = pat
    # Model, search_education çağırmadan mevcut bir içerik id'si uyduruyor -> atılmalı
    provider, _ = provider_with([final("Rutin oluşturmak işe yarar.", source_ids=[edu.id, 42])])
    reply = handle_message(db, p, "ipucu ver", provider=provider)
    assert reply.sources == []


def test_patient_id_is_bound_server_side(db, pat, client):
    p, _ = pat
    other_user, _ = register(client, "baska@example.com")
    add_plan(db, other_user["patient_id"], name="GizliIlac")
    provider, fake = provider_with([
        response([tool_use("t1", "get_medications", {"patient_id": other_user["patient_id"]}),
                  tool_use("t2", "get_adherence", {"days": 7, "patient_id": other_user["patient_id"]})],
                 stop="tool_use"),
        final(),
    ])
    handle_message(db, p, "ilaçlarım?", provider=provider)
    meds, adh = (json.loads(r["content"]) for r in tool_results(fake.calls[1]))
    assert meds["medications"] == [] and adh["planned"] == 0
    assert "GizliIlac" not in json.dumps(fake.calls, default=str)


def test_invalid_tool_input_is_clamped_and_unknown_tool_is_error(db, pat):
    p, _ = pat
    provider, fake = provider_with([
        response([tool_use("t1", "get_adherence", {"days": 365}), tool_use("t2", "drop_tables", {})],
                 stop="tool_use"),
        final(),
    ])
    handle_message(db, p, "nasılım", provider=provider)
    adh, unknown = tool_results(fake.calls[1])
    assert json.loads(adh["content"])["period_days"] == 30
    assert unknown["is_error"] is True


def test_usage_is_recorded_across_iterations(db, pat):
    p, _ = pat
    provider, _ = provider_with([
        response([tool_use("t1", "get_adherence", {"days": 7})], stop="tool_use", inp=1000, out=100,
                 cache_write=2000),
        final(inp=300, out=150, cache_read=2000),
    ])
    handle_message(db, p, "nasılım", provider=provider)
    row = db.query(LlmUsage).one()
    assert row.purpose == "coach" and row.patient_id == p.id and row.model == "claude-opus-5-5"
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (1300, 250, 2000, 2000)
    assert row.cost_usd == pytest.approx(cost_usd("claude-opus-5-5", 1300, 250, 2000, 2000))
    assert row.cost_usd == pytest.approx((1300 * 4 + 2000 * 5 + 2000 * 0.2 + 250 * 20) / 1e6)


def test_history_is_included(db, pat):
    p, _ = pat
    for sender, content in [(Sender.coach, "eski koç"), (Sender.patient, "dün unuttum"), (Sender.coach, "olur böyle")]:
        db.add(CoachMessage(patient_id=p.id, sender=sender, content=content))
    db.commit()
    provider, fake = provider_with([final()])
    handle_message(db, p, "bugün aldım", provider=provider)
    msgs = fake.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]  # baştaki koç mesajı atlanır
    assert msgs[0]["content"] == "dün unuttum"
    assert msgs[-1]["content"][-1]["text"] == "bugün aldım"


def test_daily_request_budget_exceeded_falls_back(db, pat):
    p, _ = pat
    add_plan(db, p.id)
    db.add(LlmUsage(patient_id=p.id, purpose="coach", model="claude-opus-5-5", input_tokens=10, output_tokens=10,
                    cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.001))
    db.commit()
    provider, fake = provider_with([final()], llm_daily_patient_requests=1)
    reply = handle_message(db, p, "nasılım", provider=provider)
    assert reply.guardrail_action == "budget_exceeded" and reply.message.endswith(BUDGET_NOTE)
    assert fake.calls == []
    assert db.query(LlmUsage).count() == 1


def test_monthly_budget_exceeded_falls_back(db, pat):
    p, _ = pat
    db.add(LlmUsage(patient_id=None, purpose="summary", model="claude-opus-5-5", input_tokens=0, output_tokens=0,
                    cache_read_tokens=0, cache_write_tokens=0, cost_usd=60.0))
    db.commit()
    provider, fake = provider_with([final()], llm_monthly_budget_usd=50.0)
    assert handle_message(db, p, "nasılım", provider=provider).guardrail_action == "budget_exceeded"
    assert fake.calls == []


def test_daily_token_budget_exceeded_falls_back(db, pat):
    p, _ = pat
    db.add(LlmUsage(patient_id=p.id, purpose="coach", model="claude-opus-5-5", input_tokens=9000, output_tokens=2000,
                    cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.1))
    db.commit()
    provider, fake = provider_with([final()], llm_daily_patient_tokens=10_000)
    assert handle_message(db, p, "nasılım", provider=provider).guardrail_action == "budget_exceeded"


@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    RuntimeError("beklenmeyen"),
])
def test_provider_exception_falls_back_to_rule_based(db, pat, error):
    p, _ = pat
    add_plan(db, p.id)
    provider, _ = provider_with([response([tool_use("t1", "get_adherence", {"days": 7})], stop="tool_use"), error])
    reply = handle_message(db, p, "nasılım", provider=provider)
    expected = RuleBasedCoach().reply(db, p, "nasılım")
    assert reply.guardrail_action == "llm_fallback" and reply.message == expected.message
    assert db.query(LlmUsage).one().input_tokens == 1000  # başarısız istekten önceki tur da kaydedilir


@pytest.mark.parametrize("bad", [
    final(stop="refusal"),
    final(stop="max_tokens"),
    response([text("bu json değil")]),
])
def test_unusable_model_output_falls_back(db, pat, bad):
    p, _ = pat
    provider, _ = provider_with([bad])
    assert handle_message(db, p, "nasılım", provider=provider).guardrail_action == "llm_fallback"


def test_loop_limit_forces_final_answer_then_falls_back(db, pat):
    p, _ = pat
    loop = response([tool_use("t", "get_adherence", {"days": 7})], stop="tool_use")
    provider, fake = provider_with([loop, loop], llm_max_tool_iterations=2)
    reply = handle_message(db, p, "nasılım", provider=provider)
    assert len(fake.calls) == 2
    assert "tool_choice" not in fake.calls[0] and fake.calls[1]["tool_choice"] == {"type": "none"}
    assert reply.guardrail_action == "llm_fallback"


def test_output_guardrail_blocks_dose_suggesting_llm_reply(db, pat):
    p, _ = pat
    provider, _ = provider_with([final("Şekerin iyi, dozunu azalt.")])
    reply = handle_message(db, p, "nasılım", provider=provider)
    assert reply.guardrail_action.startswith("blocked_output") and "azalt" not in reply.message


def test_input_guardrail_runs_before_llm(db, pat):
    p, _ = pat
    provider, fake = provider_with([final()])
    reply = handle_message(db, p, "Göğsümde baskı var", provider=provider)
    assert reply.risk_level == "emergency" and fake.calls == []
    assert handle_message(db, p, "Metformin dozumu artırabilir miyim?", provider=provider).guardrail_action == "dose_request"
    assert fake.calls == [] and db.query(LlmUsage).count() == 0


def test_llm_emergency_reply_escalates_with_critical_alert(db, pat):
    from app.models import Alert

    p, _ = pat
    provider, _ = provider_with([final("Lütfen hemen 112'yi ara.", risk="emergency", escalate=False)])
    reply = handle_message(db, p, "kendimi çok tuhaf hissediyorum", provider=provider)
    assert reply.escalate_to_clinician is True
    assert db.query(Alert).filter_by(kind="coach_emergency").count() == 1


def test_no_server_fallbacks_uses_plain_messages_endpoint(db, pat):
    p, _ = pat
    client = FakeClient([final()])
    client.beta = None  # beta yolu kullanılırsa test patlar
    provider = ClaudeCoachProvider(client=client, settings=llm_settings(llm_server_fallbacks=False, llm_effort=None))
    handle_message(db, p, "merhaba", provider=provider)
    call = client.calls[0]
    assert "fallbacks" not in call and "effort" not in call["output_config"]


def test_chat_endpoint_uses_provider_dependency(client, db, patient):
    from app.main import app

    _, h = patient
    fake = FakeClient([final("Merhaba! Bugün ilacını aldın mı?")])
    app.dependency_overrides[get_coach_provider] = lambda: ClaudeCoachProvider(client=fake, settings=llm_settings())
    r = client.post("/coach/chat", headers=h, json={"message": "merhaba"})
    assert r.status_code == 200 and r.json()["message"] == "Merhaba! Bugün ilacını aldın mı?"
    assert len(fake.calls) == 1


def test_get_coach_provider_selection(monkeypatch):
    import app.services.coach as coach

    # Testlerde varsayılan: kural tabanlı (conftest anahtarı boşaltır)
    assert isinstance(get_coach_provider(), RuleBasedCoach)
    monkeypatch.setattr(coach, "get_settings", lambda: Settings(anthropic_api_key="sk-x", coach_provider="auto"))
    assert isinstance(coach.get_coach_provider(), ClaudeCoachProvider)
    monkeypatch.setattr(coach, "get_settings", lambda: Settings(anthropic_api_key="sk-x", coach_provider="rule"))
    assert isinstance(coach.get_coach_provider(), RuleBasedCoach)
    monkeypatch.setattr(coach, "get_settings", lambda: Settings(anthropic_api_key="", coach_provider="claude"))
    assert isinstance(coach.get_coach_provider(), RuleBasedCoach)


def test_pricing_table():
    assert cost_usd("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert cost_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.2)
    assert cost_usd("bilinmeyen-model", 1_000_000, 0) == pytest.approx(FALLBACK_PRICE.input)
