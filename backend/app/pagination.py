"""Cursor (keyset) sayfalama.

Liste uçlarının gövdesi geriye dönük uyumluluk için dizi olarak kalır; sonraki sayfa başlıklarla bildirilir:
    X-Next-Cursor: <opak cursor>
    Link: <...?cursor=<opak cursor>&limit=N>; rel="next"
Son sayfada bu başlıklar yoktur. Cursor, sıralama anahtarının son değerini ve satır kimliğini taşır
(base64url JSON); sayfa sınırı OFFSET'e göre değil anahtara göre çizildiği için araya yeni kayıt girse de
sayfalar kaymaz. Sıralama anahtarı (ör. ölçüm zamanı) + id tekildir.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import Select, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session

MAX_PAGE = 200


def _enc_value(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def encode_cursor(values: Sequence[Any]) -> str:
    raw = json.dumps([_enc_value(v) for v in values], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, parsers: Sequence[Callable[[Any], Any]]) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != len(parsers):
            raise ValueError
        return [p(v) for p, v in zip(parsers, values, strict=True)]
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz cursor") from None


def _parser_for(col: InstrumentedAttribute) -> Callable[[Any], Any]:
    py = col.type.python_type
    if py is datetime:
        return datetime.fromisoformat
    if py is date:
        return date.fromisoformat
    return py


def paginate(db: Session, stmt: Select, keys: Sequence[InstrumentedAttribute], *, descending: bool,
             cursor: str | None, limit: int) -> tuple[list, str | None]:
    """`stmt`'yi `keys` (son eleman tekil id) sırasıyla sayfalar. Döndürür: (satırlar, sonraki cursor | None)."""
    if cursor:
        values = decode_cursor(cursor, [_parser_for(k) for k in keys])
        cmp = tuple_(*keys) < tuple_(*values) if descending else tuple_(*keys) > tuple_(*values)
        stmt = stmt.where(cmp)
    order = [k.desc() if descending else k.asc() for k in keys]
    rows = list(db.scalars(stmt.order_by(*order).limit(limit + 1)))
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    last = rows[-1]
    return rows, encode_cursor([getattr(last, k.key) for k in keys])


def set_next_page(response: Response, request: Request, next_cursor: str | None, limit: int) -> None:
    if not next_cursor:
        return
    url = request.url.include_query_params(cursor=next_cursor, limit=limit)
    response.headers["X-Next-Cursor"] = next_cursor
    response.headers["Link"] = f'<{url.path}?{url.query}>; rel="next"'
