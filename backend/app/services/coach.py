"""Koç servisi.

Akış: Katman 1 (girdi guardrail, run_input_stage) -> Provider (Claude tool-use koç veya kural tabanlı koç)
      -> Katman 3 (çıktı guardrail) -> kayıt + gerekirse hekime alert (run_provider_stage).
Senkron sohbet handle_message ile iki aşamayı art arda çalıştırır; asenkron işler (app/services/jobs.py)
girdi aşamasını istekte, provider aşamasını kuyruk işçisinde (app/worker.py) çalıştırır.

Provider seçimi get_coach_provider(): ANTHROPIC_API_KEY varsa ve COACH_PROVIDER != "rule" ise
ClaudeCoachProvider (app/services/llm/coach_agent.py), aksi halde RuleBasedCoach. Her iki provider da
aynı CoachReply şemasını döndürür; LLM hatasında/bütçe aşımında kural tabanlı koça düşülür.
"""

import logging
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
from app.services.rag import search_education

logger = logging.getLogger(__name__)


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
    """Prototip koç: LLM kullanmadan, uyum verisine dayalı motivasyon mesajı üretir.

    Hastanın mesajıyla ilgili kürate eğitim içeriği varsa (RAG) en uygun maddeyi kaynak olarak ekler;
    böylece LLM anahtarı olmadan da yanıtlar veriye dayalı ve kaynaklıdır.
    """

    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply:
        if _is_small_talk(message):
            return self._greeting(db, patient)
        hits = search_education(db, message, k=1)
        if hits:
            # Soruyla ilgili içerik öne; uyum durumu tek cümle (her mesajda aynı paragrafı tekrarlamamak için)
            hit = hits[0]
            sentences = [x.strip().rstrip(".") for x in hit.body.split(". ") if x.strip()]
            status = self._status_line(db, patient)
            reply = self._adherence_reply(db, patient)
            reply.message = f"İlgili bilgi — {hit.title}: {'. '.join(sentences[:2])}." + (f" {status}" if status else "")
            reply.sources = [Source(title=f"{hit.title} ({hit.source_name})", url=hit.source_url)]
            return reply
        return self._adherence_reply(db, patient)

    def _greeting(self, db: Session, patient: Patient) -> CoachReply:
        status = self._status_line(db, patient)
        text = ("Merhaba! Ben ilaç uyumu koçunum. İlaçlarını, ölçümlerini ya da günlük alışkanlıklarını "
                "sorabilirsin." + (f" {status}" if status else ""))
        return CoachReply(message=text)

    @staticmethod
    def _status_line(db: Session, patient: Patient) -> str:
        adh = get_adherence(db, patient.id, days=7)
        if adh.score is None:
            return ""
        return f"Son 7 günde {adh.planned} dozun {adh.taken} tanesini almışsın (%{adh.percent})."

    def _adherence_reply(self, db: Session, patient: Patient) -> CoachReply:
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


_SMALL_TALK = {
    "selam", "merhaba", "mrb", "slm", "sa", "selamun aleykum", "gunaydin", "iyi aksamlar", "iyi geceler",
    "nasilsin", "naber", "tesekkurler", "tesekkur ederim", "sagol", "sag ol", "eyvallah", "tamam", "ok",
}


def _is_small_talk(message: str) -> bool:
    text = gr.normalize(message).strip(" .!?")
    return text in _SMALL_TALK or (len(text.split()) <= 3 and text.startswith(("selam", "merhaba")))


def get_coach_provider() -> CoachProvider:
    """FastAPI bağımlılığı: yapılandırmaya göre koç sağlayıcısı (testlerde override edilebilir)."""
    if get_settings().llm_enabled:
        from app.services.llm.coach_agent import ClaudeCoachProvider

        return ClaudeCoachProvider()
    return RuleBasedCoach()


# --------- Orkestrasyon ---------


def _alert(db: Session, patient_id: int, severity: Severity, kind: str, message: str) -> None:
    db.add(Alert(patient_id=patient_id, severity=severity, kind=kind, message=message))


ESCALATION_DEDUP = timedelta(hours=24)


def _open_escalation_exists(db: Session, patient_id: int) -> bool:
    """Son 24 saatte açık (onaylanmamış) bir koç yönlendirme uyarısı var mı? Varsa yenisi açılmaz:
    her mesajda uyarı açmak hekim panelinde uyarı yorgunluğu yaratır. Acil uyarılar bu kurala tabi değildir."""
    since = datetime.now(UTC) - ESCALATION_DEDUP
    return db.scalar(
        select(Alert.id).where(
            Alert.patient_id == patient_id, Alert.kind == "coach_escalation",
            Alert.acknowledged_at.is_(None), Alert.created_at >= since,
        ).limit(1)
    ) is not None


def save_patient_message(db: Session, patient: Patient, message: str) -> None:
    """Hasta mesajını oturuma ekler (commit etmez)."""
    db.add(CoachMessage(patient_id=patient.id, sender=Sender.patient, content=message))


def _save_coach_reply(db: Session, patient: Patient, reply: CoachReply) -> None:
    db.add(
        CoachMessage(
            patient_id=patient.id, sender=Sender.coach, content=reply.message, risk_level=reply.risk_level,
            escalate_to_clinician=reply.escalate_to_clinician,
            sources=[s.model_dump() for s in reply.sources], guardrail_action=reply.guardrail_action,
        )
    )


def run_input_stage(db: Session, patient: Patient, message: str) -> CoachReply | None:
    """Katman 1 (deterministik girdi guardrail'i). Acil belirti / kendine zarar / doz sorusu tetiklenirse
    güvenli yanıtı döndürür, gerekli alert'i ve koç yanıtını oturuma ekler (commit çağıranda);
    tetiklenmezse None döner ve mesaj provider aşamasına gider. Bu aşama asla kuyrukta beklemez."""
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
        return None
    _save_coach_reply(db, patient, reply)
    return reply


def run_provider_stage(
    db: Session, patient: Patient, message: str, provider: CoachProvider | None = None, *, commit: bool = True
) -> CoachReply:
    """Provider (LLM veya kural tabanlı) -> Katman 3 (çıktı guardrail) -> koç yanıtı kaydı + gerekirse alert.

    Hasta mesajını KAYDETMEZ (çağıran kaydeder). Senkron sohbet ve kuyruk işçisi aynı hattı kullanır.
    commit=False ise commit çağırana bırakılır (işçi, iş durumunu aynı işlemde günceller)."""
    provider = provider or RuleBasedCoach()
    try:
        reply = provider.reply(db, patient, message)
    except Exception as exc:  # sağlayıcı kendi içinde düşemezse son güvenlik ağı
        logger.warning("Koç sağlayıcısı hata verdi (%s), kural tabanlı koça düşülüyor", type(exc).__name__)
        reply = RuleBasedCoach().reply(db, patient, message).model_copy(update={"guardrail_action": "llm_fallback"})
    out = gr.check_output(reply.message)
    if not out.allowed:
        reply = CoachReply(
            message=gr.BLOCKED_OUTPUT_REPLY, risk_level=RiskLevel.medium, escalate_to_clinician=False,
            guardrail_action=f"{out.action.value}:{out.category}",
        )
    elif reply.risk_level == RiskLevel.emergency:
        _alert(db, patient.id, Severity.critical, "coach_emergency", "Koç yanıtı acil yönlendirme içeriyor.")
    elif reply.escalate_to_clinician:
        if _open_escalation_exists(db, patient.id):
            # Hekim zaten bilgilendirildi; "Hekiminize bildirildi" notu yalnızca yeni uyarıda gösterilir
            reply = reply.model_copy(update={"escalate_to_clinician": False})
        else:
            _alert(db, patient.id, Severity.warning, "coach_escalation", "Koç hekime yönlendirme önerdi.")
    _save_coach_reply(db, patient, reply)
    if commit:
        db.commit()
    return reply


def handle_message(db: Session, patient: Patient, message: str, provider: CoachProvider | None = None) -> CoachReply:
    """Senkron koç hattı (POST /coach/chat, eval_coach ve testler): girdi aşaması + provider aşaması."""
    save_patient_message(db, patient, message)
    reply = run_input_stage(db, patient, message)
    if reply is not None:
        db.commit()
        return reply
    return run_provider_stage(db, patient, message, provider)
