"""Kayıt, giriş, token yenileme ve çıkış.

Oturum: kısa ömürlü erişim JWT'si + döndürülen (rotation) opak yenileme token'ı. Ayrıntı: app/security.py.

İstemci IP'si ve X-Forwarded-For güven varsayımı:
    İstek sınırı için IP, TCP bağlantısının karşı ucudur (`request.client.host`). Yalnızca bu adres
    `TRUSTED_PROXIES` içindeyse (varsayılan: aynı makinedeki Next.js sunucusu) X-Forwarded-For başlığının
    EN SAĞDAKİ değeri istemci IP'si kabul edilir; Next.js giriş/proxy rotaları bu başlığı tek değerle
    gönderir. Güvenilen proxy'ye doğrudan erişebilen biri başlığı taklit edebileceğinden API portu dışarıya
    açılmamalıdır. E-posta başına sınır IP'den bağımsız olarak her durumda uygulanır.
"""

import ipaddress
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select, update

from app.config import get_settings
from app.deps import DB, CurrentUser, TokenPayload
from app.models import Clinician, Patient, RefreshToken, Role, User
from app.schemas import RegisterIn, UserOut
from app.security import (
    create_access_token,
    deny_jti,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.services.ratelimit import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: Role
    expires_in: int  # erişim token'ı ömrü (saniye)
    refresh_expires_in: int  # yenileme token'ı ömrü (saniye)


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


def to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role,
        patient_id=user.patient.id if user.patient else None,
        clinician_id=user.clinician.id if user.clinician else None,
    )


# --- İstemci IP'si ---


@lru_cache(maxsize=32)
def _trusted_networks(entries: tuple[str, ...]) -> tuple:
    nets = []
    for e in entries:
        try:
            nets.append(ipaddress.ip_network(e.strip(), strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted_proxy(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _trusted_networks(tuple(get_settings().trusted_proxies)))


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for", "")
        last = xff.split(",")[-1].strip() if xff else ""
        if last:
            try:
                return str(ipaddress.ip_address(last))
            except ValueError:
                pass
    return peer


# --- Token üretimi ---


def _aware(dt: datetime) -> datetime:
    # SQLite saat dilimini saklamaz; karşılaştırma için UTC varsayılır
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _issue_pair(db, user: User) -> tuple[TokenPairOut, RefreshToken]:
    s = get_settings()
    raw = new_refresh_token()
    rt = RefreshToken(
        user_id=user.id, token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days),
    )
    db.add(rt)
    db.flush()
    out = TokenPairOut(
        access_token=create_access_token(user.id, user.role.value), refresh_token=raw, role=user.role,
        expires_in=s.jwt_expire_minutes * 60, refresh_expires_in=s.refresh_token_days * 86400,
    )
    return out, rt


def _revoke_all(db, user_id: int) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def _invalid_refresh() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum yenilenemedi, lütfen tekrar giriş yapın")


# --- Uç noktalar ---


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DB, request: Request):
    s = get_settings()
    check_rate_limit(f"register:ip:{client_ip(request)}", s.register_ip_limit, s.register_ip_window_seconds)
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


@router.post("/login", response_model=TokenPairOut)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB, request: Request):
    s = get_settings()
    email = form.username.strip().lower()
    # Parola denetiminden ÖNCE sayılır: başarılı/başarısız her deneme sınıra dahildir
    check_rate_limit(f"login:ip:{client_ip(request)}", s.login_ip_limit, s.login_ip_window_seconds)
    check_rate_limit(f"login:email:{email}", s.login_email_limit, s.login_email_window_seconds)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-posta veya şifre hatalı")
    out, _ = _issue_pair(db, user)
    db.commit()
    return out


@router.post("/refresh", response_model=TokenPairOut)
def refresh(data: RefreshIn, db: DB):
    """Yenileme token'ını döndürür: eskisi iptal edilir, yeni çift verilir.

    İptal edilmiş bir token tekrar gelirse token çalınmış sayılır ve kullanıcının tüm yenileme token'ları
    iptal edilir (meşru kullanıcı da yeniden giriş yapmak zorunda kalır).
    """
    rt = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(data.refresh_token)).with_for_update()
    )
    if rt is None:
        raise _invalid_refresh()
    if rt.revoked_at is not None:
        _revoke_all(db, rt.user_id)
        db.commit()
        raise _invalid_refresh()
    if _aware(rt.expires_at) <= datetime.now(UTC):
        raise _invalid_refresh()
    user = db.get(User, rt.user_id)
    if user is None:
        raise _invalid_refresh()
    out, new_rt = _issue_pair(db, user)
    rt.revoked_at = datetime.now(UTC)
    rt.replaced_by = new_rt.id
    db.commit()
    return out


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: DB, user: CurrentUser, payload: TokenPayload,
    data: Annotated[LogoutIn | None, Body()] = None,
):
    """Erişim token'ını kalan ömrü boyunca geçersiz kılar ve (verildiyse) yenileme token'ını iptal eder."""
    deny_jti(payload["jti"], float(payload["exp"]))
    if data is not None and data.refresh_token:
        rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(data.refresh_token)))
        if rt is not None and rt.user_id == user.id and rt.revoked_at is None:
            rt.revoked_at = datetime.now(UTC)
            db.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return to_user_out(user)
