from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.deps import DB, get_own_patient, require_roles
from app.models import CoachMessage, Role, User
from app.schemas import ChatIn, CoachMessageOut
from app.services.coach import CoachProvider, CoachReply, get_coach_provider, handle_message

router = APIRouter(prefix="/coach", tags=["coach"])

PatientUser = Annotated[User, Depends(require_roles(Role.patient))]
Provider = Annotated[CoachProvider, Depends(get_coach_provider)]


@router.post("/chat", response_model=CoachReply)
def chat(data: ChatIn, db: DB, user: PatientUser, provider: Provider):
    """Koç sohbeti. Yanıt şeması: message, risk_level, escalate_to_clinician, sources.
    Guardrail katmanları her mesajda çalışır; LLM (Claude tool-use) yapılandırılmışsa kullanılır,
    hata veya maliyet limiti aşımında kural tabanlı koça düşülür (guardrail_action: llm_fallback / budget_exceeded)."""
    patient = get_own_patient(db, user)
    return handle_message(db, patient, data.message, provider=provider)


@router.get("/history", response_model=list[CoachMessageOut])
def history(db: DB, user: PatientUser, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    patient = get_own_patient(db, user)
    msgs = db.scalars(
        select(CoachMessage).where(CoachMessage.patient_id == patient.id)
        .order_by(CoachMessage.created_at.desc(), CoachMessage.id.desc()).limit(limit)
    ).all()
    return list(reversed(msgs))
