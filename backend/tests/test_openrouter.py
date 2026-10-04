"""OpenRouter adaptörü testleri — httpx MockTransport ile (ağ çağrısı YOK)."""

import json

import httpx
import pytest

from app.config import Settings
from app.models import EducationContent, LlmUsage, Patient
from app.services.coach import handle_message
from app.services.llm.coach_agent import COACH_OUTPUT_SCHEMA, COACH_TOOLS, ClaudeCoachProvider
from app.services.llm.openrouter import OpenRouterClient, build_payload, convert_response
from tests.test_coach_llm import add_plan


def or_settings(**kw) -> Settings:
    base = {"openrouter_api_key": "or-test", "anthropic_api_key": "", "coach_provider": "auto"}
    return Settings(**{**base, **kw})


def chat_response(content=None, tool_calls=None, finish="stop", cost=0.0012, prompt=900, completion=120):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "model": "anthropic/claude-sonnet-5.5",
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost,
                  "prompt_tokens_details": {"cached_tokens": 100}},
    }


def mock_client(responses: list, sent: list, settings: Settings | None = None, sleeps: list | None = None) -> OpenRouterClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        item = responses.pop(0)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=item)

    s = settings or or_settings()
    http = httpx.Client(base_url=s.openrouter_base_url, transport=httpx.MockTransport(handler))
    return OpenRouterClient(s, http=http, sleep=(sleeps.append if sleeps is not None else lambda _: None))


# ---------------- Ayar çözümleme ----------------
def test_backend_resolution_and_default_model():
    assert or_settings().llm_backend == "openrouter"
    assert or_settings().model_name == "anthropic/claude-sonnet-5.5"
    assert or_settings(llm_model="anthropic/claude-haiku-4.5").model_name == "anthropic/claude-haiku-4.5"
    both = Settings(openrouter_api_key="or", anthropic_api_key="sk", llm_provider="anthropic")
    assert both.llm_backend == "anthropic" and both.model_name == "claude-opus-5-5"
    assert Settings(openrouter_api_key="", anthropic_api_key="").llm_enabled is False
    assert or_settings(coach_provider="rule").llm_enabled is False


# ---------------- İstek çevirisi ----------------
def test_payload_translation():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "bağlam"}, {"type": "text", "text": "nasılım?"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "bakıyorum"},
            {"type": "tool_use", "id": "t1", "name": "get_adherence", "input": {"days": 7}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"score": 0.8}'}]},
    ]
    p = build_payload(
        or_settings(),
        system=[{"type": "text", "text": "SİSTEM", "cache_control": {"type": "ephemeral"}}],
        messages=messages, tools=COACH_TOOLS, tool_choice={"type": "none"},
        cache_control={"type": "ephemeral"},
        output_config={"format": {"type": "json_schema", "schema": COACH_OUTPUT_SCHEMA}},
    )
    assert p["model"] == "anthropic/claude-sonnet-5.5"
    assert p["provider"] == {"data_collection": "deny"}  # sağlık verisi: saklamayan sağlayıcılar
    assert p["usage"] == {"include": True}
    assert "cache_control" not in p and "output_config" not in p
    assert p["messages"][0] == {"role": "system", "content": "SİSTEM"}
    assert p["messages"][1] == {"role": "user", "content": "bağlam\n\nnasılım?"}
    assert p["messages"][2]["tool_calls"][0]["function"] == {"name": "get_adherence", "arguments": '{"days": 7}'}
    assert p["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": '{"score": 0.8}'}
    assert p["tool_choice"] == "none"
    assert {t["function"]["name"] for t in p["tools"]} == {t["name"] for t in COACH_TOOLS}
    assert all(t["function"]["strict"] for t in p["tools"])
    assert p["response_format"]["json_schema"]["schema"] == COACH_OUTPUT_SCHEMA


def test_data_collection_can_be_disabled():
    assert "provider" not in build_payload(or_settings(openrouter_data_collection=""), messages=[])


# ---------------- Yanıt çevirisi ----------------
def test_response_translation_tool_calls_and_usage():
    r = convert_response(chat_response(tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "get_medications", "arguments": "{}"}}
    ], finish="tool_calls"))
    assert r.stop_reason == "tool_use"
    assert r.content[0].type == "tool_use" and r.content[0].name == "get_medications" and r.content[0].input == {}
    assert r.usage.input_tokens == 800 and r.usage.cache_read_input_tokens == 100
    assert r.usage.cost == pytest.approx(0.0012)


@pytest.mark.parametrize("finish,stop", [("length", "max_tokens"), ("content_filter", "refusal"), ("stop", "end_turn")])
def test_finish_reason_mapping(finish, stop):
    assert convert_response(chat_response(content="x", finish=finish)).stop_reason == stop


# ---------------- Uçtan uca koç döngüsü ----------------
def test_coach_loop_over_openrouter(db, patient):
    pat = db.get(Patient, patient[0]["patient_id"])
    add_plan(db, pat.id)
    edu = EducationContent(title="Rutine bağlama", body="İlacı kahvaltıya bağla.", source_name="WHO",
                           source_url="https://www.who.int", tags="uyum")
    db.add(edu)
    db.commit()
    final = {"message": "Son 7 günde dozlarının çoğunu almışsın, harika!", "risk_level": "low",
             "escalate_to_clinician": False, "source_ids": [edu.id, 999]}
    sent: list = []
    client = mock_client([
        chat_response(tool_calls=[
            {"id": "a", "type": "function", "function": {"name": "get_adherence", "arguments": '{"days": 7}'}},
            {"id": "b", "type": "function", "function": {"name": "search_education", "arguments": '{"query": "rutin"}'}},
        ], finish="tool_calls", cost=0.001),
        chat_response(content=json.dumps(final, ensure_ascii=False), cost=0.002),
    ], sent)
    provider = ClaudeCoachProvider(client=client, settings=or_settings())

    reply = handle_message(db, pat, "Bu hafta nasılım?", provider=provider)

    assert reply.message.startswith("Son 7 günde")
    assert [s.title for s in reply.sources] == ["Rutine bağlama"]  # uydurma id 999 atıldı
    # ikinci istekte araç sonuçları "tool" mesajı olarak gönderildi
    tool_msgs = [m for m in sent[1]["messages"] if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"a", "b"}
    # hasta adı/e-postası modele gitmedi
    assert "Ayse" not in json.dumps(sent, ensure_ascii=False) and "ayse@example.com" not in json.dumps(sent)
    usage = db.query(LlmUsage).one()
    assert usage.cost_usd == pytest.approx(0.003)  # OpenRouter'ın bildirdiği gerçek maliyet


def test_openrouter_http_error_falls_back_to_rule_based(db, patient):
    pat = db.get(Patient, patient[0]["patient_id"])
    add_plan(db, pat.id)
    provider = ClaudeCoachProvider(client=mock_client([500, 500], []), settings=or_settings())
    reply = handle_message(db, pat, "Bu hafta nasılım?", provider=provider)
    assert reply.guardrail_action == "llm_fallback"
    assert "dozun" in reply.message


def test_input_guardrail_still_runs_before_openrouter(db, patient):
    pat = db.get(Patient, patient[0]["patient_id"])
    sent: list = []
    provider = ClaudeCoachProvider(client=mock_client([], sent), settings=or_settings())
    reply = handle_message(db, pat, "göğsümde baskı var", provider=provider)
    assert reply.risk_level == "emergency" and sent == []  # LLM'e hiç istek gitmedi


def test_rate_limit_429_is_retried_then_succeeds():
    sent, sleeps = [], []
    client = mock_client([429, chat_response(content="tamam")], sent, sleeps=sleeps)
    r = client.messages.create(messages=[{"role": "user", "content": "selam"}])
    assert r.content[0].text == "tamam" and len(sent) == 2 and sleeps == [5.0]


def test_rate_limit_gives_up_after_retries():
    from app.services.llm.openrouter import OpenRouterError

    client = mock_client([429, 429, 429], [], sleeps=[])
    with pytest.raises(OpenRouterError, match="429"):
        client.messages.create(messages=[{"role": "user", "content": "selam"}])


def test_free_models_are_priced_zero():
    from app.services.llm.pricing import cost_usd

    assert cost_usd("qwen/qwen3.8-27b:free", 100_000, 10_000) == 0.0
