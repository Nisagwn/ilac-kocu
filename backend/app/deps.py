"""Kimlik doğrulama ve rol bazlı erişim bağımlılıkları.

Kural (docs/M1/03-veri-gizliligi.md):
- Hasta: yalnızca kendi kaydı, okuma + yazma
- Hekim: kendisine atanmış hastalar, okuma (+ uyarı kapatma)
- Bakım veren: bağlı olduğu hastalar, yalnızca okuma
Yetkisiz erişimde kaynağın varlığını sızdırmamak için 404 döner.

Erişim kaydı (KVKK hesap verebilirlik): hekim/bakım veren `get_patient_for_read` ile bir hastanın verisine
eriştiğinde `AccessLog` satırı yazılır (istek başına hasta başına bir kez; yalnızca üst veri).
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AccessLog, Patient, PatientCaregiver, Role, User
from app.security import decode_token, is_jti_denied

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")

DB = Annotated[Session, Depends(get_db)]


def _cred_exc() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Geçersiz veya süresi dolmuş oturum",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_token_payload(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    """Erişim JWT'sini doğrular: imza, süre, tür ve çıkışla iptal edilmemiş olması (jti iptal listesi)."""
    try:
        payload = decode_token(token)
        int(payload["sub"])
        jti = payload["jti"]
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise _cred_exc() from None
    if payload.get("typ", "access") != "access" or is_jti_denied(jti):
        raise _cred_exc()
    return payload


TokenPayload = Annotated[dict, Depends(get_token_payload)]

# Oturum (Session) istek başına açıldığı için istek kapsamlı erişim kaydı bağlamı session.info'da tutulur.
_AUDIT_CTX = "audit_ctx"


def get_current_user(request: Request, db: DB, payload: TokenPayload) -> User:
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise _cred_exc()
    # Yeni istek: erişim kaydı bağlamını sıfırla (aynı istekte aynı hasta için tek satır yazılır)
    db.info[_AUDIT_CTX] = {"path": request.url.path[:255], "method": request.method, "seen": set()}
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role):
    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu işlem için yetkiniz yok")
        return user

    return checker


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Hasta bulunamadı")


def can_read_patient(db: Session, user: User, patient: Patient) -> bool:
    if user.role == Role.patient:
        return patient.user_id == user.id
    if user.role == Role.clinician:
        return user.clinician is not None and patient.clinician_id == user.clinician.id
    if user.role == Role.caregiver:
        link = db.get(PatientCaregiver, (patient.id, user.id))
        return link is not None
    return False


def _record_access(db: Session, user: User, patient: Patient) -> None:
    """Hasta dışındaki bir aktörün erişimini kaydeder. Sağlık verisi değil, yalnızca üst veri yazılır."""
    if user.role == Role.patient:
        return
    ctx = db.info.get(_AUDIT_CTX)
    if ctx is None:  # HTTP isteği dışında (betik/test yardımcıları) kayıt yok
        return
    key = (user.id, patient.id)
    if key in ctx["seen"]:
        return
    ctx["seen"].add(key)
    db.add(AccessLog(
        actor_user_id=user.id, actor_role=user.role.value, patient_id=patient.id,
        action="read" if ctx["method"] in ("GET", "HEAD") else "write", path=ctx["path"],
    ))
    db.commit()


def get_patient_for_read(db: Session, user: User, patient_id: int, *, audit: bool = True) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None or not can_read_patient(db, user, patient):
        raise _not_found()
    if audit:
        _record_access(db, user, patient)
    return patient


def get_patient_for_write(db: Session, user: User, patient_id: int) -> Patient:
    """Hasta verisini yalnızca hastanın kendisi yazabilir."""
    patient = get_patient_for_read(db, user, patient_id, audit=False)
    if user.role != Role.patient:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu veriyi yalnızca hasta değiştirebilir")
    return patient


def get_own_patient(db: Session, user: User) -> Patient:
    if user.role != Role.patient:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Yalnızca hastalar için")
    patient = db.scalar(select(Patient).where(Patient.user_id == user.id))
    if patient is None:
        raise _not_found()
    return patient
