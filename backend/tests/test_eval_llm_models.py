"""scripts/eval_llm_models.py — yalnızca sahte LLM istemcisiyle (ağ çağrısı YOK, gerçek API'ye karşı koşmaz)."""

import json
from types import SimpleNamespace

from scripts import eval_llm_models as ev
from tests.test_coach_llm import final, response, text, tool_use


class SmartFakeClient:
    """Sahte model: önce araçları çağırır, sonra araç sonuçlarındaki sayılarla şemaya uygun yanıt verir.
    `broken=True` iken nihai yanıt şema dışıdır (şema uyumsuzluğu ölçümü)."""

    def __init__(self, broken=False):
        self.broken = broken
        self.calls = 0
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls += 1
        last = kwargs["messages"][-1]["content"]
        results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"] \
            if isinstance(last, list) else []
        if not results:
            query = next((b["text"] for b in reversed(last) if isinstance(b, dict) and b.get("type") == "text"), "")
            return response([tool_use("t1", "get_adherence", {"days": 7}),
                             tool_use("t2", "get_measurements", {"days": 7, "type": "all"}),
                             tool_use("t3", "search_education", {"query": query})],
                            stop="tool_use", model=kwargs["model"])
        if self.broken:
            return response([text("bu json değil")], model=kwargs["model"])
        adh = json.loads(results[0]["content"])
        edu = json.loads(results[2]["content"]).get("results", [])
        msg = (f"Son 7 günde {adh['planned']} dozun {adh['taken']} tanesini almışsın (%{adh['percent']}); "
               f"{adh['missed']} doz kaçmış. İlacını bir rutine bağlamak işe yarar.")
        return final(msg, risk="low", source_ids=[e["id"] for e in edu[:1]] or [0], model=kwargs["model"])


def test_eval_llm_models_with_mock_clients(tmp_path, capsys):
    out = tmp_path / "llm-karsilastirma.md"
    clients = {"mock/iyi": SmartFakeClient(), "mock/bozuk:free": SmartFakeClient(broken=True)}
    code = ev.main(["--models", "mock/iyi,mock/bozuk:free", "--out", str(out), "--guardrail-limit", "12"],
                   client_factory=lambda s: clients[s.llm_model])
    assert code == 0
    md = out.read_text(encoding="utf-8")
    assert "| `mock/iyi` |" in md and "| `mock/bozuk:free` |" in md
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    good, bad = (r["metrics"] for r in data["reports"])
    assert good["tool_use"] == 1.0 and good["schema_violation"] == 0.0 and good["sources"] == 1.0
    assert good["guardrail"] == 1.0 and good["p95_seconds"] is not None
    assert bad["schema_violation"] == 1.0 and bad["meets_thresholds"] is False
    assert data["reports"][1]["cost_usd"] == 0.0  # ":free" modeller ücretsiz
    assert clients["mock/iyi"].calls > 0


def test_eval_llm_models_without_api_key_exits_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ev.main(["--models", "x/y", "--out", str(tmp_path / "r.md")]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def test_report_thresholds():
    r = ev.ModelReport(model="m", llm_messages=50, tool_checked=10, tool_ok=9, schema_errors=1,
                       guardrail_total=5, guardrail_passed=5, latencies=[1.0] * 19 + [12.0])
    assert r.tool_use == 0.9 and r.schema_violation == 0.02 and r.p95_seconds == 1.0
    assert r.meets_thresholds()
    r.guardrail_passed = 4
    assert not r.meets_thresholds()
