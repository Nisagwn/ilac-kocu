"""Koç kalite değerlendirme seti (tests/coach_eval_scenarios.yaml) için ortak yardımcılar.

- load_scenarios(): YAML'ı okur
- build_patient(): senaryonun sentetik hasta verisini kurar (her planlı doz kayıtlı: günün saatinden bağımsız)
- check_tools(): araç çıktılarının (get_adherence / get_measurements) beklenen sayıları verdiğini denetler
- check_reply(): yanıtın veriyi yansıttığını, kaynakların doğrulandığını, doz önermediğini ve risk düzeyini denetler

tests/test_coach_eval.py (kural tabanlı koç, CI) ve scripts/eval_llm_models.py (gerçek LLM) aynı denetimleri kullanır.
Yalnızca sentetik veri.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCENARIOS_PATH = BACKEND_DIR / "tests" / "coach_eval_scenarios.yaml"

WHO_TITLE_PREFIX = "WHO"


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def _local_today():
    from app.config import get_settings

    return datetime.now(UTC).astimezone(ZoneInfo(get_settings().timezone)).date()


def build_patient(db, scenario: dict, idx: int = 0):
    """Senaryonun sentetik hastasını kurar ve Patient döndürür (commit eder)."""
    from app.models import (
        DoseLog,
        DoseStatus,
        Measurement,
        MeasurementType,
        Medication,
        MedicationSchedule,
        Patient,
        Role,
        User,
    )

    setup = scenario.get("setup") or {}
    user = User(email=f"eval-{scenario['id'].lower()}-{idx}@example.com", password_hash="-",
                full_name=f"Sentetik {scenario['id']}", role=Role.patient)
    db.add(user)
    db.flush()
    patient = Patient(user_id=user.id, birth_year=1960, has_diabetes=True, has_hypertension=True,
                      consent_at=datetime.now(UTC))
    db.add(patient)
    db.flush()

    today = _local_today()
    for med_spec in setup.get("medications") or []:
        med = Medication(patient_id=patient.id, name=med_spec["name"], active_ingredient=med_spec.get("ingredient"),
                         dose_text="(sentetik)", active=True)
        db.add(med)
        db.flush()
        log_days = int(med_spec.get("log_days", 7))
        missed = {str(k): set(v) for k, v in (med_spec.get("missed") or {}).items()}
        for hhmm in med_spec["times"]:
            h, m = (int(x) for x in hhmm.split(":"))
            sched = MedicationSchedule(medication_id=med.id, time_of_day=time(h, m), days_of_week="1234567",
                                       start_date=today - timedelta(days=log_days - 1))
            db.add(sched)
            db.flush()
            for ago in range(log_days):
                status = DoseStatus.skipped if ago in missed.get(hhmm, set()) else DoseStatus.taken
                db.add(DoseLog(patient_id=patient.id, schedule_id=sched.id,
                               scheduled_date=today - timedelta(days=ago), status=status))

    now = datetime.now(UTC)
    for mtype, values in (setup.get("measurements") or {}).items():
        for i, raw in enumerate(values):
            if isinstance(raw, str) and "/" in raw:
                sys_v, dia_v = (float(x) for x in raw.split("/"))
            else:
                sys_v, dia_v = float(raw), None
            unit = "mmHg" if mtype == "blood_pressure" else "mg/dL"
            db.add(Measurement(patient_id=patient.id, type=MeasurementType(mtype), value=sys_v, value2=dia_v,
                               unit=unit, measured_at=now - timedelta(days=i + 1, hours=1)))
    db.commit()
    return patient


# ------------------------------------------------------------------ araç çıktıları
def check_tools(db, patient_id: int, expect: dict) -> list[str]:
    from app.services.llm import patient_data

    fails: list[str] = []
    days = int(expect.get("adherence_days", 7))
    adh = patient_data.adherence_payload(db, patient_id, days)
    for key, want in (expect.get("adherence") or {}).items():
        if adh.get(key) != want:
            fails.append(f"get_adherence.{key}={adh.get(key)!r}, beklenen {want!r}")
    for slot, want in (expect.get("missed_by_time_of_day") or {}).items():
        got = adh["missed_by_time_of_day"].get(slot)
        if got is None or any(got.get(k) != v for k, v in want.items()):
            fails.append(f"missed_by_time_of_day[{slot}]={got!r}, beklenen {want!r}")
    by_med = {m["medication"]: m for m in adh["by_medication"]}
    for name, want in (expect.get("by_medication") or {}).items():
        got = by_med.get(name)
        if got is None or any(got.get(k) != v for k, v in want.items()):
            fails.append(f"by_medication[{name}]={got!r}, beklenen {want!r}")
    if expect.get("measurements"):
        meas = {t["type"]: t for t in patient_data.measurements_payload(db, patient_id, 7, "all")["types"]}
        for mtype, want in expect["measurements"].items():
            got = meas.get(mtype)
            if got is None or any(got.get(k) != v for k, v in want.items()):
                fails.append(f"get_measurements[{mtype}]={ {k: (got or {}).get(k) for k in want}!r}, beklenen {want!r}")
    return fails


# ------------------------------------------------------------------ yanıt
def _verified_sources(db, sources) -> list[str]:
    """Doğrulanamayan kaynak başlıkları (WHO veya veritabanındaki eğitim içeriği dışında kalanlar)."""
    from sqlalchemy import select

    from app.models import EducationContent

    titles = set(db.scalars(select(EducationContent.title)))
    bad = []
    for s in sources:
        title = s.title if hasattr(s, "title") else s["title"]
        if title.startswith(WHO_TITLE_PREFIX):
            continue
        if not any(title == t or title.startswith(t + " (") for t in titles):
            bad.append(title)
    return bad


def check_reply(db, reply, expect: dict, *, rule_based: bool = True) -> list[str]:
    """Yanıt denetimleri. rule_based=False (LLM) iken birebir ifade (reply_contains) yerine reply_numbers aranır."""
    from app.services import guardrails as gr

    fails: list[str] = []
    text = reply.message
    if rule_based:
        for frag in expect.get("reply_contains") or []:
            if frag not in text:
                fails.append(f"yanıt '{frag}' içermiyor")
    else:
        nums = expect.get("reply_numbers") or []
        if nums and not any(re.search(rf"(?<!\d){re.escape(n)}(?!\d)", text) for n in nums):
            fails.append(f"yanıt beklenen sayılardan hiçbirini içermiyor: {nums}")
        if "112" in (expect.get("reply_contains") or []) and "112" not in text:
            fails.append("acil yanıtta 112 yok")

    if expect.get("guardrail_action") and reply.guardrail_action != expect["guardrail_action"]:
        fails.append(f"guardrail_action={reply.guardrail_action!r}, beklenen {expect['guardrail_action']!r}")
    if not gr.check_output(text).allowed and not (reply.guardrail_action or "").startswith("blocked"):
        fails.append("yanıt çıktı guardrail'ine takılıyor (doz önerisi / teşhis)")
    if "risk_level" in expect and reply.risk_level.value != expect["risk_level"]:
        fails.append(f"risk_level={reply.risk_level.value}, beklenen {expect['risk_level']}")
    if "escalate" in expect and reply.escalate_to_clinician != expect["escalate"]:
        fails.append(f"escalate={reply.escalate_to_clinician}, beklenen {expect['escalate']}")

    sources = list(reply.sources)
    bad = _verified_sources(db, sources)
    if bad:
        fails.append(f"doğrulanmamış kaynak: {bad}")
    kind = expect.get("sources")
    titles = [s.title for s in sources]
    if kind == "none" and sources:
        fails.append(f"kaynak beklenmiyordu: {titles}")
    elif kind == "who" and not any(t.startswith(WHO_TITLE_PREFIX) for t in titles):
        if rule_based or not sources:
            fails.append(f"WHO kaynağı yok: {titles}")
    elif kind in ("education", "any") and not sources:
        fails.append("kaynak boş")
    elif kind == "education":
        edu = [t for t in titles if not t.startswith(WHO_TITLE_PREFIX)]
        if not edu:
            fails.append(f"eğitim içeriği kaynağı yok: {titles}")
        elif expect.get("source_title") and rule_based and not any(t.startswith(expect["source_title"]) for t in edu):
            fails.append(f"kaynak {edu}, beklenen '{expect['source_title']}'")
    return fails
