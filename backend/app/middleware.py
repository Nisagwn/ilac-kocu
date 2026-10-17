"""API platform katmanı: saf ASGI middleware'ler (BaseHTTPMiddleware'in akış sorunlarını taşımazlar).

- `RateLimitMiddleware`: token bucket (services/ratelimit.take_token). Kimlik: geçerli erişim token'ı varsa
  kullanıcı, yoksa istemci IP'si. Her yanıtta `X-RateLimit-Limit/Remaining/Reset`; aşımda 429 + `Retry-After`.
  Koça mesaj gönderme (POST /coach/chat, /coach/jobs) muaftır: acil belirti yanıtı asla sınıra takılmamalı.
- `IdempotencyMiddleware`: `Idempotency-Key` başlıklı POST/PUT/PATCH/DELETE isteklerinde aynı anahtarla gelen
  tekrar isteğe saklanan yanıt döner (`Idempotent-Replayed: true`); farklı gövdeyle aynı anahtar 422, ilk istek
  sürerken gelen tekrar 409. Yalnızca giriş yapmış kullanıcılar için (anahtar kullanıcı başına tekildir).
- `ETagMiddleware`: başarılı GET yanıtlarına içerik özetinden `ETag` ve `Cache-Control: private, no-cache`;
  `If-None-Match` eşleşirse gövdesiz 304.
- `DeprecationMiddleware`: sürümsüz eski yollara (`/patients/...`) `Deprecation` ve `/v1` karşılığını gösteren
  `Link: <...>; rel="successor-version"` başlıkları.

Sağlık/izleme uçları (/health*, /metrics) bu katmanların dışında tutulur.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app.config import get_settings
from app.db import middleware_session
from app.security import decode_token, is_jti_denied
from app.services import idempotency
from app.services.ratelimit import take_token

EXEMPT_PREFIXES = ("/health", "/metrics")
# Koça mesaj gönderme genel kovaya takılmaz: acil belirti mesajına güvenlik yanıtı her zaman dönmeli.
# Bu uçların kendi hasta başına sınırı vardır (routers/coach.py) ve o sınır acil mesajları muaf tutar.
_RATE_LIMIT_EXEMPT_POSTS = ("/coach/chat", "/coach/jobs")
API_VERSION_PREFIX = "/v1"
_UNVERSIONED_OK = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")
_IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_\-:.]{8,255}$")
_STORED_HEADERS = {b"content-type", b"location"}


def _exempt(path: str) -> bool:
    return path.startswith(EXEMPT_PREFIXES)


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return None


def authenticated_user_id(scope) -> int | None:
    """Geçerli (imzası doğru, süresi dolmamış, iptal edilmemiş) erişim token'ının kullanıcısı; yoksa None."""
    auth = _header(scope, b"authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = decode_token(token.strip())
        if payload.get("typ", "access") != "access" or is_jti_denied(payload["jti"]):
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


async def _send_json(send, status: int, body: dict, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()), *(headers or [])]})
    await send({"type": "http.response.body", "body": raw})


# ---------------------------------------------------------------- istek sınırı
class RateLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        s = get_settings()
        if (scope["type"] != "http" or not s.api_rate_limit_enabled or _exempt(scope["path"])
                or scope.get("method") == "OPTIONS"
                or (scope.get("method") == "POST" and scope["path"].endswith(_RATE_LIMIT_EXEMPT_POSTS))):
            await self.app(scope, receive, send)
            return

        from app.routers.auth import client_ip  # döngüsel içe aktarmayı önler

        uid = authenticated_user_id(scope)
        key = f"api:u:{uid}" if uid is not None else f"api:ip:{client_ip(Request(scope))}"
        result = await run_in_threadpool(take_token, key, s.api_rate_limit_capacity,
                                         s.api_rate_limit_refill_per_second)
        rl_headers = [
            (b"x-ratelimit-limit", str(result.limit).encode()),
            (b"x-ratelimit-remaining", str(result.remaining).encode()),
            (b"x-ratelimit-reset", str(result.reset_after).encode()),
        ]
        if not result.allowed:
            await _send_json(send, 429, {"detail": "Çok fazla istek gönderildi. Lütfen biraz sonra tekrar deneyin."},
                             [*rl_headers, (b"retry-after", str(result.retry_after).encode())])
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *rl_headers]}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------- idempotency
class IdempotencyMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        key = _header(scope, b"idempotency-key") if scope["type"] == "http" else None
        if key is None or scope.get("method") not in _IDEMPOTENT_METHODS:
            await self.app(scope, receive, send)
            return
        key = key.strip()
        if not _IDEMPOTENCY_KEY.match(key):
            await _send_json(send, 400, {"detail": "Idempotency-Key 8-255 karakter olmalı (harf, rakam, - _ : .)"})
            return
        uid = authenticated_user_id(scope)
        if uid is None:  # kimlik yoksa anahtar yok sayılır; istek normal işlenir (ör. 401)
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)

        s = get_settings()
        ttl = timedelta(hours=s.idempotency_ttl_hours)
        fastapi_app = scope["app"]
        fp = idempotency.request_fingerprint(scope["method"], scope["path"],
                                             scope.get("query_string", b"").decode("latin-1"), body)

        def _begin():
            with middleware_session(fastapi_app) as db:
                return idempotency.begin(db, uid, key, scope["method"], scope["path"], fp, ttl)

        res = await run_in_threadpool(_begin)
        if res.outcome == "mismatch":
            await _send_json(send, 422, {"detail": "Bu Idempotency-Key başka bir istekle kullanılmış"})
            return
        if res.outcome == "in_progress":
            await _send_json(send, 409, {"detail": "Aynı Idempotency-Key ile bir istek hâlâ işleniyor"},
                             [(b"retry-after", b"1")])
            return
        if res.outcome == "replay":
            raw = (res.body or "").encode()
            headers = [(k.encode(), v.encode()) for k, v in (res.headers or {}).items()]
            if res.status_code not in (204, 304):
                headers.append((b"content-length", str(len(raw)).encode()))
            await send({"type": "http.response.start", "status": res.status_code,
                        "headers": [*headers, (b"idempotent-replayed", b"true")]})
            await send({"type": "http.response.body", "body": b"" if res.status_code in (204, 304) else raw})
            return

        sent_body = False

        async def replay_receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        captured = {"status": 500, "headers": {}, "chunks": [], "size": 0, "too_big": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["headers"] = {k.decode("latin-1"): v.decode("latin-1")
                                       for k, v in message.get("headers", []) if k.lower() in _STORED_HEADERS}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                captured["size"] += len(chunk)
                if captured["size"] > s.idempotency_max_body_bytes:
                    captured["too_big"], captured["chunks"] = True, []
                elif not captured["too_big"]:
                    captured["chunks"].append(chunk)
            await send(message)

        try:
            await self.app(scope, replay_receive, send_wrapper)
        except BaseException:
            await run_in_threadpool(_with_session, fastapi_app, idempotency.abandon, res.record_id)
            raise
        stored = None if captured["too_big"] else b"".join(captured["chunks"]).decode("utf-8", "replace")
        await run_in_threadpool(_with_session, fastapi_app, idempotency.complete, res.record_id,
                                captured["status"], stored, captured["headers"])


def _with_session(fastapi_app, fn, *args):
    with middleware_session(fastapi_app) as db:
        return fn(db, *args)


# ---------------------------------------------------------------- ETag / 304
def _etag_matches(if_none_match: str, etag: str) -> bool:
    if if_none_match.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(t.strip().removeprefix("W/") == bare for t in if_none_match.split(","))


class ETagMiddleware:
    CACHE_CONTROL = b"private, no-cache"

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "GET" or _exempt(scope["path"]):
            await self.app(scope, receive, send)
            return

        start: dict = {}
        chunks: list[bytes] = []

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                start.update(message)
                return
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                await self._finish(scope, send, start, b"".join(chunks))
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _finish(self, scope, send, start: dict, body: bytes) -> None:
        headers = [(k, v) for k, v in start.get("headers", [])]
        names = {k.lower() for k, _ in headers}
        if b"cache-control" not in names:
            headers.append((b"cache-control", self.CACHE_CONTROL))
        status = start.get("status", 200)
        if status == 200:
            etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
            headers.append((b"etag", etag.encode()))
            inm = _header(scope, b"if-none-match")
            if inm and _etag_matches(inm, etag):
                keep = [(k, v) for k, v in headers if k.lower() not in (b"content-length", b"content-type")]
                await send({"type": "http.response.start", "status": 304, "headers": keep})
                await send({"type": "http.response.body", "body": b""})
                return
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------- sürümsüz yollar
class DeprecationMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path or path.startswith((API_VERSION_PREFIX + "/", *_UNVERSIONED_OK)) or path == API_VERSION_PREFIX:
            await self.app(scope, receive, send)
            return
        link = f"<{API_VERSION_PREFIX}{path}>; rel=\"successor-version\"".encode("latin-1", "replace")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), (b"deprecation", b"true"),
                                                  (b"link", link)]}
            await send(message)

        await self.app(scope, receive, send_wrapper)
