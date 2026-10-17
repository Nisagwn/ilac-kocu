from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.deps import DB, get_own_patient, require_roles
from app.models import CoachMessage, Role, User
from app.pagination import paginate, set_next_cursor_headers
from app.redis_client import get_redis
from app.schemas import ChatIn, CoachMessageOut
from app.services import guardrails as gr
from app.services import jobs
from app.services.coach import CoachProvider, CoachReply, get_coach_provider, handle_message
from app.services.ratelimit import check_rate_limit

router = APIRouter(prefix="/coach", tags=["coach"])

PatientUser = Annotated[User, Depends(require_roles(Role.patient))]
Provider = Annotated[CoachProvider, Depends(get_coach_provider)]

COACH_RATE_LIMIT = 10  # hasta başına dakikada koç mesajı
# Acil belirti / kendine zarar mesajları istek sınırına takılmaz: güvenlik yanıtı her zaman döner
_NEVER_LIMITED = {gr.Action.emergency, gr.Action.self_harm}


@router.post("/chat", response_model=CoachReply)
def chat(data: ChatIn, db: DB, user: PatientUser, provider: Provider):
    """Senkron koç sohbeti (testler ve scripts/eval_coach.py için; arayüz /coach/jobs kullanır).
    Yanıt şeması: message, risk_level, escalate_to_clinician, sources.
    Guardrail katmanları her mesajda çalışır; LLM yapılandırılmışsa kullanılır, hata veya maliyet limiti
    aşımında kural tabanlı koça düşülür (guardrail_action: llm_fallback / budget_exceeded)."""
    patient = get_own_patient(db, user)
    return handle_message(db, patient, data.message, provider=provider)


@router.post(
    "/jobs", response_model=jobs.CoachJobOut,
    responses={202: {"model": jobs.CoachJobOut, "description": "İş kuyruğa alındı; GET /coach/jobs/{id} ile yoklayın"}},
)
def create_job(
    data: ChatIn, db: DB, user: PatientUser, provider: Provider, response: Response, background: BackgroundTasks,
):
    """Asenkron koç mesajı.

    Girdi guardrail'i (acil belirti, kendine zarar, doz sorusu) senkron çalışır: tetiklenirse 200 ve
    `status: "done"` ile yanıt hemen döner (kuyrukta beklemez). Aksi halde iş oluşturulur ve 202
    `{job_id, status: "queued"}` döner; yanıt kuyruk işçisinde (python -m app.worker) üretilir.
    Redis yoksa iş aynı süreçte arka plan görevi olarak işlenir. Hasta başına dakikada 10 mesaj sınırı."""
    patient = get_own_patient(db, user)
    if gr.check_input(data.message).action not in _NEVER_LIMITED:
        check_rate_limit(f"coach:patient:{patient.id}", COACH_RATE_LIMIT, 60)

    reply, job = jobs.submit_coach_message(db, patient, data.message)
    if job is None:
        return jobs.CoachJobOut(status=jobs.DONE, reply=reply)

    r = get_redis()
    if r is None or not jobs.enqueue_job(r, job.id):
        background.add_task(jobs.run_job_inline, db.get_bind(), job.id, provider)
    response.status_code = status.HTTP_202_ACCEPTED
    return jobs.CoachJobOut(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=jobs.CoachJobOut)
def get_job(job_id: str, db: DB, user: PatientUser):
    """Koç işinin durumu: queued | running | done (reply dolu) | failed (error dolu). Yalnızca işin sahibi."""
    patient = get_own_patient(db, user)
    job = jobs.get_patient_job(db, patient, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "İş bulunamadı")
    db.refresh(job)  # işçi başka bir oturumda güncellemiş olabilir
    return jobs.job_out(job)


@router.get("/history", response_model=list[CoachMessageOut])
def history(
    db: DB, user: PatientUser, request: Request, response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
):
    """Son `limit` mesaj, sohbet sırasıyla (eskiden yeniye). Daha eski mesajlar varsa `X-Next-Cursor` başlığı
    döner; `?cursor=` ile bir önceki sayfa (daha eski mesajlar) alınır."""
    patient = get_own_patient(db, user)
    q = select(CoachMessage).where(CoachMessage.patient_id == patient.id)
    msgs, next_cursor = paginate(db, q, CoachMessage.created_at, CoachMessage.id, limit=limit, cursor=cursor)
    set_next_cursor_headers(request, response, next_cursor)
    return list(reversed(msgs))
