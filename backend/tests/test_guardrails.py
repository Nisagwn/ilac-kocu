from pathlib import Path

import pytest
import yaml

from app.services.guardrails import Action, check_input, check_output, normalize

SCENARIOS = yaml.safe_load((Path(__file__).parent / "guardrail_scenarios.yaml").read_text(encoding="utf-8"))


def test_scenario_set_size():
    assert len(SCENARIOS) == 40
    assert len({s["id"] for s in SCENARIOS}) == 40


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_input_guardrail(sc):
    result = check_input(sc["message"])
    assert result.action == Action(sc["expected"]), f"{sc['id']}: {sc['message']!r} -> {result}"


def test_normalize_turkish():
    assert normalize("GÖĞSÜMDE BASKI") == "gogsumde baski"
    assert normalize("İlacımı  Iğdır'da") == "ilacimi igdir'da"


@pytest.mark.parametrize(
    "reply",
    [
        "Dozunu 1000 mg'a çıkarabilirsin.",
        "Bence dozunu azalt, şekerin iyi görünüyor.",
        "Şekerin iyi, ilacını bırakabilirsin.",
        "Unuttuysan iki doz al, sorun olmaz.",
        "Bu değerlerle sende böbrek hastalığı olabilir.",
        "Ben bir doktorum, bana güvenebilirsin.",
        "Akşam dozunu atlayabilirsin.",
    ],
)
def test_output_guardrail_blocks(reply):
    assert check_output(reply).action == Action.blocked_output


@pytest.mark.parametrize(
    "reply",
    [
        "Son 7 günde 14 dozun 12 tanesini almışsın, harika gidiyorsun!",
        "İlacını kahvaltıya bağlamak hatırlamayı kolaylaştırabilir.",
        "Bu konuyu bir sonraki kontrolünde hekimine sorabilirsin.",
        "Düzenli yürüyüş kan şekeri kontrolüne yardımcı olabilir.",
    ],
)
def test_output_guardrail_allows(reply):
    assert check_output(reply).allowed
