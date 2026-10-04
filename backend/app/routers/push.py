"""Web push abonelikleri (yalnızca hasta). Hatırlatmalar: app/services/reminders.py"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import DB, require_roles
from app.models import PushSubscription, Role, User
from app.schemas import PushSubscribeIn, PushTestOut, PushUnsubscribeIn, VapidKeyOut
from app.services.push import PushSender, send_to_user, webpush_sender

router = APIRouter(prefix="/push", tags=["push"])

PatientUser = Annotated[User, Depends(require_roles(Role.patient))]


def _not_configured() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Web push yapılandırılmamış (VAPID anahtarları eksik)")


def get_push_sender() -> PushSender:
    """Varsayılan pywebpush göndericisi; testlerde dependency_overrides ile değiştirilir."""
    if not get_settings().vapid_configured:
        raise _not_configured()
    return webpush_sender


@router.get("/vapid-public-key", response_model=VapidKeyOut)
def vapid_public_key(user: PatientUser):
    key = get_settings().vapid_public_key
    if not key:
        raise _not_configured()
    return VapidKeyOut(public_key=key)


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def subscribe(data: PushSubscribeIn, db: DB, user: PatientUser):
    """Tarayıcı aboneliğini kaydeder; aynı endpoint varsa günceller (upsert)."""
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=data.endpoint)
        db.add(sub)
    sub.user_id = user.id
    sub.p256dh = data.keys.p256dh
    sub.auth = data.keys.auth
    db.commit()


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(data: PushUnsubscribeIn, db: DB, user: PatientUser):
    sub = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == data.endpoint, PushSubscription.user_id == user.id)
    )
    if sub is not None:
        db.delete(sub)
        db.commit()


@router.post("/test", response_model=PushTestOut)
def send_test(db: DB, user: PatientUser, sender: Annotated[PushSender, Depends(get_push_sender)]):
    """Kullanıcının aboneliklerine deneme bildirimi yollar."""
    payload = {"title": "İlaç Koçu", "body": "Bildirimler açık. Doz saatlerinde hatırlatma alacaksınız.",
               "url": "/bugun"}
    sent = send_to_user(db, user.id, payload, sender)
    db.commit()
    return PushTestOut(sent=sent)
