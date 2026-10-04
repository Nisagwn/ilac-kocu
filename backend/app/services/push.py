"""Web push gönderimi (Web Push Protocol + VAPID, pywebpush).

Gönderici (`PushSender`) enjekte edilebilir: testler sahte gönderici kullanır, ağa çıkılmaz.
"""

import json
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushSubscription

log = logging.getLogger(__name__)


class PushGone(Exception):
    """Abonelik artık geçerli değil (push servisi 404/410 döndü) → silinmeli."""


class PushFailed(Exception):
    """Geçici gönderim hatası; abonelik korunur."""


# (subscription_info, payload_json) -> None; hata durumunda PushGone / PushFailed fırlatır
PushSender = Callable[[dict, str], None]


def subscription_info(sub: PushSubscription) -> dict:
    return {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}


def webpush_sender(info: dict, payload: str) -> None:
    """Varsayılan gönderici: pywebpush ile VAPID imzalı bildirim yollar."""
    from pywebpush import WebPushException, webpush

    s = get_settings()
    try:
        webpush(
            subscription_info=info,
            data=payload,
            vapid_private_key=s.vapid_private_key,
            vapid_claims={"sub": s.vapid_subject},  # pywebpush sözlüğü değiştirir; her çağrıda yenisi
            ttl=60 * 60,
            timeout=10,
        )
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            raise PushGone(str(e)) from e
        raise PushFailed(str(e)) from e
    except Exception as e:  # ağ hataları vb.
        raise PushFailed(str(e)) from e


def send_to_user(db: Session, user_id: int, payload: dict, sender: PushSender) -> int:
    """Kullanıcının tüm aboneliklerine bildirim yollar; geçersiz abonelikleri siler.
    Başarılı gönderim sayısını döner. Silme işlemleri için commit çağıranın sorumluluğundadır."""
    data = json.dumps(payload, ensure_ascii=False)
    sent = 0
    for sub in db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all():
        try:
            sender(subscription_info(sub), data)
            sent += 1
        except PushGone:
            log.info("Geçersiz push aboneliği silindi (id=%s)", sub.id)
            db.delete(sub)
        except PushFailed as e:
            log.warning("Push gönderilemedi (id=%s): %s", sub.id, e)
    return sent
