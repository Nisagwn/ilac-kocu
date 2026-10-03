"""Koç servisi.

Akış: Katman 1 (girdi guardrail) -> Provider (M2/M3: kural tabanlı prototip, M4: Claude tool-use)
      -> Katman 3 (çıktı guardrail) -> kayıt + gerekirse hekime alert.

Provider arayüzü sabittir; M4'te ClaudeCoachProvider aynı CoachReply şemasını döndürecek.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Alert,
    CoachMessage,
    DoseLog,
    DoseStatus,
    Medication,
    MedicationSchedule,
    Patient,
    RiskLevel,
    Sender,
    Severity,
)
from app.services import guardrails as gr
from app.services.adherence import AdherenceResult, LogSpec, ScheduleSpec, compute_adherence


class Source(BaseModel):
    title: str
    url: str | None = None


class CoachReply(BaseModel):
    """LLM'in structured output şeması (spec: message, risk_level, escalate_to_clinician, sources)."""

    message: str
    risk_level: RiskLevel = RiskLevel.low
    escalate_to_clinician: bool = False
    sources: list[Source] = Field(default_factory=list)
    guardrail_action: str | None = None


# --------- Tool fonksiyonları (M4'te LLM tool'u olarak da kullanılacak) ---------


def get_adherence(db: Session, patient_id: int, days: int = 7, now: datetime | None = None) -> AdherenceResult:
    # İlaç saatleri hastanın yerel saatindedir
    now = (now or datetime.now(UTC)).astimezone(ZoneInfo(get_settings().timezone))
    end = now.date()
    start = end - timedelta(days=days - 1)

    rows = db.execute(
        select(MedicationSchedule, Medication.id)
        .join(Medication)
        .where(Medication.patient_id == patient_id, Medication.active.is_(True))
    ).all()
    schedules = [
        ScheduleSpec(s.id, med_id, s.time_of_day, s.days_of_week, s.start_date) for s, med_id in rows
    ]
    logs = [
        LogSpec(lg.schedule_id, lg.scheduled_date, lg.status == DoseStatus.taken)
        for lg in db.scalars(
            select(DoseLog).where(
                DoseLog.patient_id == patient_id,
                DoseLog.scheduled_date >= start,
                DoseLog.scheduled_date <= end,
            )
        )
    ]
    return compute_adherence(schedules, logs, start, end, as_of=now)


# --------- Provider ---------


class CoachProvider(Protocol):
    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply: ...


WHO_SOURCE = Source(
    title="WHO — Adherence to long-term therapies: evidence for action (2003)",
    url="https://iris.who.int/handle/10665/42682",
)


class RuleBasedCoach:
    """Prototip koç: LLM kullanmadan, uyum verisine dayalı motivasyon mesajı üretir."""

    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply:
        adh = get_adherence(db, patient.id, days=7)
        if adh.score is None:
            return CoachReply(
                message="Henüz ilaç planın görünmüyor. Planını ekledikten sonra ilerlemeni birlikte takip edebiliriz.",
            )
        missed = adh.planned - adh.taken
        if missed == 0:
            text = (
                f"Son 7 günde planlanan {adh.planned} dozun hepsini almışsın, harika gidiyorsun! "
                "Bu düzeni korumak için ilacını her gün aynı rutine bağlamaya devam et."
            )
            return CoachReply(message=text, sources=[WHO_SOURCE])
        text = (
            f"Son 7 günde {adh.planned} dozun {adh.taken} tanesini almışsın (%{adh.percent}). "
            f"{missed} doz kaçmış görünüyor. Küçük bir ipucu: ilacını kahvaltı gibi her gün yaptığın "
            "bir işe bağlamak ya da telefonuna alarm kurmak hatırlamayı kolaylaştırır."
        )
        risk = RiskLevel.medium if adh.score < 0.6 else RiskLevel.low
        return CoachReply(message=text, risk_level=risk, escalate_to_clinician=adh.score < 0.5, sources=[WHO_SOURCE])


# --------- Orkestrasyon ---------


def _alert(db: Session, patient_id: int, severity: Severity, kind: str, message: str) -> None:
    db.add(Alert(patient_id=patient_id, severity=severity, kind=kind, message=message))


def handle_message(db: Session, patient: Patient, message: str, provider: CoachProvider | None = None) -> CoachReply:
    provider = provider or RuleBasedCoach()
    db.add(CoachMessage(patient_id=patient.id, sender=Sender.patient, content=message))

    check = gr.check_input(message)
    if check.action == gr.Action.self_harm:
        reply = CoachReply(
            message=gr.SELF_HARM_REPLY, risk_level=RiskLevel.emergency, escalate_to_clinician=True,
            guardrail_action=check.action.value,
        )
        _alert(db, patient.id, Severity.critical, "self_harm", "Hasta kendine zarar verme ile ilgili ifade kullandı.")
    elif check.action == gr.Action.emergency:
        reply = CoachReply(
            message=gr.EMERGENCY_REPLY, risk_level=RiskLevel.emergency, escalate_to_clinician=True,
            guardrail_action=check.action.value,
        )
        _alert(db, patient.id, Severity.critical, "emergency_symptom", f"Acil belirti bildirimi ({check.category}).")
    elif check.action == gr.Action.dose_request:
        reply = CoachReply(
            message=gr.DOSE_REPLY, risk_level=RiskLevel.medium, escalate_to_clinician=True,
            guardrail_action=check.action.value,
        )
        _alert(db, patient.id, Severity.info, "dose_question", "Hasta ilaç dozu/değişikliği hakkında soru sordu.")
    else:
        reply = provider.reply(db, patient, message)
        out = gr.check_output(reply.message)
        if not out.allowed:
            reply = CoachReply(
                message=gr.BLOCKED_OUTPUT_REPLY, risk_level=RiskLevel.medium, escalate_to_clinician=False,
                guardrail_action=f"{out.action.value}:{out.category}",
            )
        elif reply.escalate_to_clinician:
            _alert(db, patient.id, Severity.warning, "coach_escalation", "Koç hekime yönlendirme önerdi (düşük uyum).")

    db.add(
        CoachMessage(
            patient_id=patient.id, sender=Sender.coach, content=reply.message, risk_level=reply.risk_level,
            escalate_to_clinician=reply.escalate_to_clinician,
            sources=[s.model_dump() for s in reply.sources], guardrail_action=reply.guardrail_action,
        )
    )
    db.commit()
    return reply
