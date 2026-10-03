"""Kimlik doğrulama ve rol bazlı erişim bağımlılıkları.

Kural (docs/M1/03-veri-gizliligi.md):
- Hasta: yalnızca kendi kaydı, okuma + yazma
- Hekim: kendisine atanmış hastalar, okuma (+ uyarı kapatma)
- Bakım veren: bağlı olduğu hastalar, yalnızca okuma
Yetkisiz erişimde kaynağın varlığını sızdırmamak için 404 döner.
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Patient, PatientCaregiver, Role, User
from app.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

DB = Annotated[Session, Depends(get_db)]


def get_current_user(db: DB, token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Geçersiz veya süresi dolmuş oturum",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise cred_exc from None
    user = db.get(User, user_id)
    if user is None:
        raise cred_exc from None
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


def get_patient_for_read(db: Session, user: User, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None or not can_read_patient(db, user, patient):
        raise _not_found()
    return patient


def get_patient_for_write(db: Session, user: User, patient_id: int) -> Patient:
    """Hasta verisini yalnızca hastanın kendisi yazabilir."""
    patient = get_patient_for_read(db, user, patient_id)
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
