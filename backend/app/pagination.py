"""Cursor (keyset) sayfalama.

Sözleşme (tüm sayfalı listeler için aynı):
- Sıra: sıralama anahtarına göre YENİDEN ESKİYE, eşitlikte id azalan. `cursor` her zaman "bu öğeden daha
  eskiler" anlamına gelir; sayfalar arasında eklenen yeni kayıtlar öğe atlatmaz/tekrarlatmaz.
- `cursor` opaktır: base64url(JSON [sıralama değeri ISO-8601, id]). İstemci içeriğine güvenmemelidir.
  Bozuk cursor → 422.
- `limit`: 1–200.
- Yeni uçlar (`/v1/doses`, `/v1/measurements`): gövde `{"items": [...], "next_cursor": "..." | null}`.
- Mevcut liste uçları (geriye dönük uyum, gövde dizi olarak kalır): `limit`/`cursor` verildiğinde sayfalı
  moda geçer; sonraki sayfa varsa `X-Next-Cursor` ve `Link: <...>; rel="next"` başlıkları eklenir.

Sıralama anahtarları: ölçüm `measured_at`, doz kaydı `scheduled_date`, uyarı ve koç mesajı `created_at`.
"""

import base64
import binascii
import json
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(sort_value: date | datetime, row_id: int) -> str:
    raw = json.dumps([sort_value.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _bad_cursor() -> HTTPException:
    return HTTPException(422, "Geçersiz sayfalama imleci (cursor)")


def decode_cursor(cursor: str, kind: type[date] | type[datetime]) -> tuple[Any, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value, row_id = json.loads(raw)
        if not isinstance(row_id, int) or not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value) if kind is datetime else date.fromisoformat(value)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        raise _bad_cursor() from None
    return parsed, row_id


def _sort_kind(column) -> type[date] | type[datetime]:
    try:
        py = column.type.python_type
    except NotImplementedError:  # pragma: no cover
        return datetime
    return datetime if issubclass(py, datetime) else date


def paginate(db: Session, query: Select, sort_col, id_col, *, limit: int, cursor: str | None) -> tuple[list, str | None]:
    """Sorguyu (sort_col DESC, id DESC) keyset ile sayfalar; (öğeler, sonraki cursor) döndürür."""
    if cursor:
        value, row_id = decode_cursor(cursor, _sort_kind(sort_col))
        query = query.where(or_(sort_col < value, and_(sort_col == value, id_col < row_id)))
    rows = db.scalars(query.order_by(sort_col.desc(), id_col.desc()).limit(limit + 1)).all()
    items = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(getattr(last, sort_col.key), last.id)
    return items, next_cursor


def set_next_cursor_headers(request: Request, response: Response, next_cursor: str | None) -> None:
    """Mevcut (dizi döndüren) uçlar için sonraki sayfa başlıkları."""
    if not next_cursor:
        return
    response.headers["X-Next-Cursor"] = next_cursor
    url = request.url.include_query_params(cursor=next_cursor)
    response.headers["Link"] = f'<{url.path}?{url.query}>; rel="next"'
