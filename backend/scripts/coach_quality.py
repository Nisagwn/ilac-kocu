"""Koç kalite değerlendirmesi (tests/coach_eval_scenarios.yaml): senaryo kurulumu, koşturma ve puanlama.

tests/test_coach_eval.py (kural tabanlı koç, CI kapısı) ve scripts/eval_llm_models.py (model
karşılaştırması) aynı kodu kullanır. Her senaryo yeni bir SENTETİK hastayla, bellek içi SQLite'ta çalışır.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import yaml

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "eval-secret-eval-secret-eval-secret-32b")
os.environ.setdefault("REMINDERS_ENABLED", "false")

SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "tests" / "coach_eval_scenarios.yaml"
EXPECT_KEYS = {"mentions_any", "contains_all", "none_of", "sources_min", "source_contains", "risk_in", "escalate",
               "guardrail_action", "max_chars"}
SETUP_KEYS = {"taken", "skipped", "skip_reason", "glucose", "education"}


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    ids = set()
    for it in items:
        missing = {"id", "category", "setup", "message", "expect"} - set(it)
        if missing:
            raise ValueError(f"{it.get('id')}: eksik alan {missing}")
        if it["id"] in ids:
            raise ValueError(f"{it['id']} iki kez tanımlı")
        ids.add(it["id"])
        if unknown := set(it["expect"]) - EXPECT_KEYS:
            raise ValueError(f"{it['id']}: bilinmeyen ölçüt {unknown}")
        if unknown := set(it["setup"]) - SETUP_KEYS:
            raise ValueError(f"{it['id']}: bilinmeyen kurulum alanı {unknown}")
        if it["setup"].get("taken", 0) + it["setup"].get("skipped", 0) > 7:
            raise ValueError(f"{it['id']}: 7 günden fazla doz")
    return items


@dataclass
class QualityResult:
    id: str
    category: str
    passed: bool
    checks: dict[str, bool]
    reply: str
    latency_ms: float
    guardrail_action: str | None
    risk_level: str | None
    escalate: bool
    sources: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def score(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 1.0


def make_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def build_patient(db, setup: dict, n: int):
    """Senaryo kurulumuna göre sentetik hasta (ad yok, e-posta sentetik)."""
    from zoneinfo import ZoneInfo

    from app.config import get_settings
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

    user = User(email=f"eval{n}@example.org", password_hash="x", full_name="Sentetik Hasta", role=Role.patient)
    db.add(user)
    db.flush()
    patient = Patient(user_id=user.id, has_diabetes=True, has_hypertension=True, consent_at=datetime.now(UTC))
    db.add(patient)
    db.flush()
    today = datetime.now(UTC).astimezone(ZoneInfo(get_settings().timezone)).date()
    if "taken" in setup or "skipped" in setup:
        med = Medication(patient_id=patient.id, name="Glifor", active_ingredient="metformin")
        sched = MedicationSchedule(time_of_day=dtime(20, 0), start_date=today - timedelta(days=30))
        med.schedules = [sched]
        db.add(med)
        db.flush()
        statuses = [DoseStatus.taken] * setup.get("taken", 0) + [DoseStatus.skipped] * setup.get("skipped", 0)
        for i, st in enumerate(statuses):  # bugünden geriye: bugünün dozu kayıtlıysa vadesinden önce de sayılır
            db.add(DoseLog(patient_id=patient.id, schedule_id=sched.id, scheduled_date=today - timedelta(days=i),
                           status=st, reason=setup.get("skip_reason") if st == DoseStatus.skipped else None))
    for i, v in enumerate(reversed(setup.get("glucose") or [])):
        db.add(Measurement(patient_id=patient.id, type=MeasurementType.glucose_fasting, value=v, unit="mg/dL",
                           measured_at=datetime.now(UTC) - timedelta(days=i, hours=2)))
    db.commit()
    return patient


def evaluate(expect: dict, reply) -> dict[str, bool]:
    from app.services import guardrails as gr

    text = reply.message or ""
    low = text.lower()
    titles = [s.title for s in reply.sources]
    checks = {"bos_degil": bool(text.strip()), "guvenli_cikti": gr.check_output(text).allowed}
    if "mentions_any" in expect:
        checks["veriye_dayali"] = any(m.lower() in low for m in expect["mentions_any"])
    if "contains_all" in expect:
        checks["icerik"] = all(m.lower() in low for m in expect["contains_all"])
    if "none_of" in expect:
        checks["yasakli_ifade_yok"] = not any(m.lower() in low for m in expect["none_of"])
    if "sources_min" in expect:
        checks["kaynak_sayisi"] = len(reply.sources) >= expect["sources_min"]
    if "source_contains" in expect:
        checks["dogru_kaynak"] = any(expect["source_contains"].lower() in t.lower() for t in titles)
    if "risk_in" in expect:
        checks["risk_duzeyi"] = getattr(reply.risk_level, "value", reply.risk_level) in expect["risk_in"]
    if "escalate" in expect:
        checks["hekime_yonlendirme"] = bool(reply.escalate_to_clinician) == bool(expect["escalate"])
    if "guardrail_action" in expect:
        checks["guardrail"] = reply.guardrail_action == expect["guardrail_action"]
    if "max_chars" in expect:
        checks["uzunluk"] = len(text) <= expect["max_chars"]
    return checks


def run(scenarios: list[dict], provider_factory=None) -> list[QualityResult]:
    """Her senaryoyu tam koç hattından geçirir. provider_factory() -> CoachProvider (None: kural tabanlı)."""
    from sqlalchemy import func, select

    from app.models import LlmUsage
    from app.seed import seed_education
    from app.services.coach import handle_message

    results = []
    for n, sc in enumerate(scenarios):
        db = make_session()
        try:
            if sc["setup"].get("education"):
                seed_education(db)
            patient = build_patient(db, sc["setup"], n)
            provider = provider_factory() if provider_factory else None
            started = time.perf_counter()
            reply = handle_message(db, patient, sc["message"], provider)
            latency = (time.perf_counter() - started) * 1000
            checks = evaluate(sc["expect"], reply)
            results.append(QualityResult(
                id=sc["id"], category=sc["category"], passed=all(checks.values()), checks=checks,
                reply=reply.message, latency_ms=round(latency, 1), guardrail_action=reply.guardrail_action,
                risk_level=getattr(reply.risk_level, "value", reply.risk_level),
                escalate=bool(reply.escalate_to_clinician), sources=[s.title for s in reply.sources],
                cost_usd=float(db.scalar(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0))) or 0.0),
            ))
        finally:
            db.close()
    return results


def summarize(results: list[QualityResult]) -> dict:
    n = len(results)
    lat = sorted(r.latency_ms for r in results)
    return {
        "total": n,
        "passed": sum(r.passed for r in results),
        "pass_rate": round(sum(r.passed for r in results) / n, 3) if n else 0.0,
        "mean_score": round(sum(r.score for r in results) / n, 3) if n else 0.0,
        "p50_ms": lat[n // 2] if n else 0.0,
        "p95_ms": lat[min(n - 1, int(n * 0.95))] if n else 0.0,
        "fallbacks": sum(r.guardrail_action == "llm_fallback" for r in results),
        "cost_usd": round(sum(r.cost_usd for r in results), 4),
    }
