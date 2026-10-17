"""API platform katmanı: sürümleme, kullanıcı başına istek sınırı, Idempotency-Key ve ETag (saf ASGI).

Sıra (dıştan içe; main.py'de `install_platform` çağrısıyla kurulur):
    RequestContext → CORS → Deprecation → RateLimit → Idempotency → ETag → uygulama

- **Sürümleme:** iş uçları `/v1` altında sunulur. Sürümsüz eski yollar bir süre çalışmaya devam eder; OpenAPI'de
  görünmez ve yanıtlarına `Deprecation: true` + `Link: </v1/...>; rel="successor-version"` eklenir.
- **İstek sınırı:** JWT'deki kullanıcı başına token bucket (app/services/ratelimit.py). Sınıflar: yazma
  (POST/PUT/PATCH/DELETE) 60/dk, okuma (GET/HEAD) 300/dk, koç mesajı (POST /coach/chat, /coach/jobs) 10/dk —
  config'ten ayarlanır, `RATE_LIMIT_ENABLED=false` ile kapanır. Acil belirti / kendine zarar içeren koç mesajları
  sınıra takılmaz (güvenlik yanıtı her zaman döner). Yanıtlarda `X-RateLimit-Limit`, `X-RateLimit-Remaining`;
  429'da `Retry-After`. Kimliksiz istekler (giriş/kayıt) burada sayılmaz; onların kendi IP/e-posta sınırı var.
- **Idempotency-Key:** doz kaydı (PUT/POST /patients/{id}/doses), ölçüm ekleme (POST /patients/{id}/measurements)
  ve koç mesajı (POST /coach/chat, /coach/jobs). Aynı kullanıcı + anahtar + aynı istek (yöntem+yol+gövde
  sha256) → saklanan yanıt aynen, `Idempotent-Replayed: true`; farklı istek → 422; ilk istek hâlâ işleniyorsa
  → 409. Yalnızca 2xx yanıtlar saklanır. Kayıtlar `IDEMPOTENCY_TTL_HOURS` (24 sa) sonra
  `purge_expired_idempotency_keys` ile temizlenir.
- **ETag:** 200 dönen GET JSON yanıtlarına zayıf ETag (gövdenin sha256'sı) ve
  `Cache-Control: private, no-cache` + `Vary: Authorization` eklenir; `If-None-Match` eşleşirse gövdesiz 304.
"""

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import get_db
from app.models import IdempotencyKey
from app.security import decode_token
from app.services import ratelimit

API_PREFIX = "/v1"

# Sürümlenmeyen sistem yolları (Deprecation başlığı ve istek sınırı uygulanmaz)
_SYSTEM_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")

MAX_BUFFERED_BODY = 1_000_000  # Idempotency/koç sınıflandırması için okunan gövde üst sınırı (bayt)

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


# ---------------------------------------------------------------- yardımcılar
def strip_version(path: str) -> str:
    if path == API_PREFIX:
        return "/"
    return path[len(API_PREFIX):] if path.startswith(API_PREFIX + "/") else path


def _is_system(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _SYSTEM_PREFIXES)


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return None


def _user_id(scope: Scope) -> int | None:
    """Bearer JWT'den kullanıcı kimliği (imza + süre doğrulanır; iptal listesine route'taki bağımlılık bakar)."""
    auth = _header(scope, b"authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = decode_token(token.strip())
        if payload.get("typ", "access") != "access":
            return None
        return int(payload["sub"])
    except Exception:
        return None


class _BodyTooLarge(Exception):
    pass


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        msg = await receive()
        if msg["type"] != "http.request":  # istemci bağlantıyı kopardı
            break
        chunk = msg.get("body", b"")
        size += len(chunk)
        if size > MAX_BUFFERED_BODY:
            raise _BodyTooLarge
        chunks.append(chunk)
        if not msg.get("more_body", False):
            break
    return b"".join(chunks)


def _replay_receive(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def _receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return _receive


def _dumps(content) -> bytes:
    # FastAPI JSONResponse ile aynı biçim
    return json.dumps(content, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")).encode()


async def _send_json(send: Send, status: int, content, extra_headers: dict[str, str] | None = None) -> None:
    body = b"" if content is None else _dumps(content)
    headers = [(b"content-length", str(len(body)).encode())]
    if content is not None:
        headers.append((b"content-type", b"application/json"))
    for k, v in (extra_headers or {}).items():
        headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _add_headers(send: Send, extra: dict[str, str]) -> Send:
    encoded = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in extra.items()]

    async def _send(message: dict) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), *encoded]}
        await send(message)

    return _send


# ---------------------------------------------------------------- sürümleme
class DeprecationMiddleware:
    """Sürümsüz (eski) iş yollarına `Deprecation: true` ve ardıl sürüm bağlantısı ekler."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path == API_PREFIX or path.startswith(API_PREFIX + "/") or _is_system(path):
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, _add_headers(send, {
            "Deprecation": "true", "Link": f'<{API_PREFIX}{path}>; rel="successor-version"',
        }))


# ---------------------------------------------------------------- istek sınırı
_COACH_PATHS = {"/coach/chat", "/coach/jobs"}


def _is_safety_message(body: bytes) -> bool:
    """Acil belirti / kendine zarar mesajı mı? (bu mesajlar asla sınırlanmaz)"""
    from app.services import guardrails as gr

    try:
        message = json.loads(body).get("message")
    except Exception:
        return False
    if not isinstance(message, str):
        return False
    return gr.check_input(message).action in (gr.Action.emergency, gr.Action.self_harm)


class RateLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        s = get_settings()
        if scope["type"] != "http" or not s.rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = strip_version(scope.get("path", ""))
        uid = _user_id(scope) if method != "OPTIONS" and not _is_system(path) else None
        if uid is None:
            await self.app(scope, receive, send)
            return

        if method in ("GET", "HEAD"):
            klass, limit = "read", s.rate_limit_read_per_minute
        elif method == "POST" and path in _COACH_PATHS:
            klass, limit = "coach", s.rate_limit_coach_per_minute
            try:
                body = await _read_body(receive)
            except _BodyTooLarge:
                await _send_json(send, 413, {"detail": "İstek gövdesi çok büyük"})
                return
            receive = _replay_receive(body, receive)
            if _is_safety_message(body):
                await self.app(scope, receive, send)
                return
        else:
            klass, limit = "write", s.rate_limit_write_per_minute

        res = await run_in_threadpool(ratelimit.hit, f"user:{uid}:{klass}", limit, 60)
        if not res.allowed:
            await _send_json(send, 429, {"detail": ratelimit.TOO_MANY_REQUESTS_DETAIL}, res.headers())
            return
        await self.app(scope, receive, _add_headers(send, res.headers()))


# ---------------------------------------------------------------- Idempotency-Key
_IDEMPOTENT_ROUTES = (
    (re.compile(r"^/patients/\d+/doses$"), {"PUT", "POST"}),
    (re.compile(r"^/patients/\d+/measurements$"), {"POST"}),
    (re.compile(r"^/coach/(chat|jobs)$"), {"POST"}),
)
_VALID_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")  # görünür ASCII, en fazla 255 karakter


def is_idempotent_route(method: str, path: str) -> bool:
    path = strip_version(path)
    return any(method in methods and rx.match(path) for rx, methods in _IDEMPOTENT_ROUTES)


def request_hash(method: str, path: str, body: bytes) -> str:
    h = hashlib.sha256()
    h.update(method.encode() + b"\n" + strip_version(path).encode() + b"\n")
    h.update(body)
    return h.hexdigest()


@contextmanager
def _session(app):
    """Uygulamanın `get_db` bağımlılığından (testlerde override edilmiş olabilir) bir oturum açar."""
    overrides = getattr(app, "dependency_overrides", {}) if app is not None else {}
    result = overrides.get(get_db, get_db)()
    if inspect.isgenerator(result):
        db = next(result)
        try:
            yield db
        finally:
            result.close()
    else:
        yield result


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _ttl() -> timedelta:
    return timedelta(hours=get_settings().idempotency_ttl_hours)


def _claim(app, user_id: int, key: str, req_hash: str) -> tuple[str, IdempotencyKey | None]:
    """Anahtarı ayırır. Dönüş: ("new"|"replay"|"mismatch"|"in_progress", kayıt)."""
    with _session(app) as db:
        for _ in range(2):
            row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
            if row is not None and _aware(row.created_at) < datetime.now(UTC) - _ttl():
                db.delete(row)  # süresi dolmuş, henüz temizlenmemiş kayıt: anahtar yeniden kullanılabilir
                db.commit()
                row = None
            if row is None:
                db.add(IdempotencyKey(user_id=user_id, key=key, request_hash=req_hash))
                try:
                    db.commit()
                    return "new", None
                except IntegrityError:  # eşzamanlı aynı anahtar: diğer istek önce ayırdı
                    db.rollback()
                    continue
            if row.request_hash != req_hash:
                return "mismatch", row
            if row.response_status is None:
                return "in_progress", row
            return "replay", row
        return "in_progress", None


def _finish(app, user_id: int, key: str, status: int | None, body: bytes) -> None:
    with _session(app) as db:
        row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
        if row is None:
            return
        stored = None
        ok = status is not None and 200 <= status < 300
        if ok and body:
            try:
                stored = json.loads(body)
            except ValueError:
                ok = False  # JSON olmayan yanıt saklanamaz
        if ok:
            row.response_status = status
            row.response_body = stored
        else:
            db.delete(row)  # başarısız istek: istemci aynı anahtarla yeniden deneyebilsin
        db.commit()


class IdempotencyMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = _header(scope, b"idempotency-key")
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if key is None or not is_idempotent_route(method, path):
            await self.app(scope, receive, send)
            return
        key = key.strip()
        if not _VALID_KEY.match(key):
            await _send_json(send, 400, {"detail": "Geçersiz Idempotency-Key (1-255 görünür ASCII karakter)"})
            return
        uid = _user_id(scope)
        if uid is None:  # kimliksiz: route 401 döndürür
            await self.app(scope, receive, send)
            return
        try:
            body = await _read_body(receive)
        except _BodyTooLarge:
            await _send_json(send, 413, {"detail": "İstek gövdesi çok büyük"})
            return
        app = scope.get("app")
        outcome, row = await run_in_threadpool(_claim, app, uid, key, request_hash(method, path, body))
        if outcome == "mismatch":
            await _send_json(send, 422, {"detail": "Bu Idempotency-Key farklı bir istekle kullanılmış"})
            return
        if outcome == "in_progress":
            await _send_json(send, 409, {"detail": "Aynı Idempotency-Key ile bir istek hâlâ işleniyor"},
                             {"Retry-After": "1"})
            return
        if outcome == "replay":
            await _send_json(send, row.response_status, row.response_body, {"Idempotent-Replayed": "true"})
            return

        captured: dict = {"status": None, "body": []}

        async def capture(message: dict) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
            elif message["type"] == "http.response.body":
                captured["body"].append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, _replay_receive(body, receive), capture)
        finally:
            await run_in_threadpool(_finish, app, uid, key, captured["status"], b"".join(captured["body"]))


def purge_expired_idempotency_keys(db: Session, older_than: timedelta | None = None) -> int:
    """Süresi dolan Idempotency-Key kayıtlarını siler (worker'dan periyodik çağrılabilir); silinen sayı."""
    cutoff = datetime.now(UTC) - (older_than if older_than is not None else _ttl())
    result = db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    db.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------- ETag / 304
def weak_etag(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    value = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        c = candidate.strip()
        if c == "*" or c.removeprefix("W/") == value:  # zayıf karşılaştırma (RFC 9110 §13.1.2)
            return True
    return False


class ETagMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Yalnız GET: HEAD yanıtının gövdesi boş olduğundan özeti GET ile tutarlı olmaz
        if scope["type"] != "http" or scope.get("method") != "GET" or _is_system(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if_none_match = _header(scope, b"if-none-match")
        state: dict = {"start": None, "eligible": False, "chunks": []}

        async def wrapped(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = dict((k.lower(), v) for k, v in message.get("headers", []))
                ctype = headers.get(b"content-type", b"").decode("latin-1")
                state["eligible"] = message["status"] == 200 and ctype.startswith("application/json")
                if state["eligible"]:
                    state["start"] = message
                    return
                await send(message)
                return
            if message["type"] != "http.response.body" or not state["eligible"]:
                await send(message)
                return
            state["chunks"].append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = b"".join(state["chunks"])
            etag = weak_etag(body)
            start = state["start"]
            keep = [(k, v) for k, v in start.get("headers", []) if k.lower() not in (b"etag", b"cache-control")]
            extra = [(b"etag", etag.encode()), (b"cache-control", b"private, no-cache"), (b"vary", b"Authorization")]
            if if_none_match and _etag_matches(if_none_match, etag):
                keep = [(k, v) for k, v in keep if k.lower() not in (b"content-length", b"content-type")]
                await send({"type": "http.response.start", "status": 304, "headers": keep + extra})
                await send({"type": "http.response.body", "body": b""})
                return
            await send({**start, "headers": keep + extra})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, wrapped)


def install_platform(app) -> None:
    """Platform middleware'lerini doğru sırayla ekler (add_middleware: son eklenen en dışta)."""
    app.add_middleware(ETagMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(DeprecationMiddleware)
