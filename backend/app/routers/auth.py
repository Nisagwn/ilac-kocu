from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.models import Clinician, Patient, Role, User
from app.schemas import RegisterIn, TokenOut, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role,
        patient_id=user.patient.id if user.patient else None,
        clinician_id=user.clinician.id if user.clinician else None,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DB):
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu e-posta ile kayıtlı bir kullanıcı var")

    user = User(
        email=data.email.lower(), password_hash=hash_password(data.password),
        full_name=data.full_name, role=data.role,
    )
    db.add(user)
    db.flush()
    if data.role == Role.patient:
        db.add(Patient(
            user_id=user.id, birth_year=data.birth_year, has_diabetes=data.has_diabetes,
            has_hypertension=data.has_hypertension, consent_at=datetime.now(UTC),
        ))
    elif data.role == Role.clinician:
        # NOT: Gerçek kullanımda hekim hesabı yönetici onayıyla açılmalı (M5 sınırlılıklar).
        db.add(Clinician(user_id=user.id, title=data.title, institution=data.institution))
    db.commit()
    db.refresh(user)
    return to_user_out(user)


@router.post("/login", response_model=TokenOut)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-posta veya şifre hatalı")
    return TokenOut(access_token=create_access_token(user.id, user.role.value), role=user.role)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return to_user_out(user)
