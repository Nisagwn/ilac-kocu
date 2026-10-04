"""Hekim için haftalık özet anlatısı.

- LLM varsa ve bütçe uygunsa: deterministik istatistiklerden kısa, olgusal Türkçe anlatı (teşhis/tedavi önerisi yok).
- Aksi halde (veya hata/guardrail engeli): deterministik şablon anlatı.
- Önbellek: (hasta, yerel gün, dönem, veri parmak izi) anahtarıyla; aynı gün aynı veri için ikinci kez ücret
  ödenmez. Redis varsa paylaşımlı (tüm worker'lar; "narr:<hasta>:<gün>:<dönem>:<parmak izi>", 24 saat TTL, JSON),
  yoksa süreç içi. Ayrıca hasta başına günlük LLM özet sayısı sınırlıdır (llm_daily_summary_requests).
"""

import hashlib
import json
import logging
import re
import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.observability import observe_llm
from app.redis_client import get_redis
from app.services import guardrails as gr
from app.services.llm.budget import UsageTotals, check_budget, record_usage
from app.services.llm.client import LlmError, create_message, get_client, text_of
from app.services.llm.patient_data import MEASUREMENT_LABELS

logger = logging.getLogger(__name__)

PURPOSE = "summary"
DISCLAIMER = "Otomatik oluşturulmuş özettir; klinik değerlendirmenin yerine geçmez."

NARRATIVE_SYSTEM_PROMPT = """Sen bir aile hekimi ya da diyabet hemşiresi için haftalık hasta özeti yazan bir \
klinik dokümantasyon asistanısın. Sana bir hastanın son dönem ilaç uyumu, ölçüm istatistikleri ve uyarı sayıları \
JSON olarak verilir.

Kurallar:
- Yalnızca verilen sayılara dayan; sayı uydurma, verilmeyen bilgiyi varsayma.
- Teşhis koyma, hastalık etiketi koyma, tedavi ya da doz önerisi verme; ilaç değişikliği önerme.
- Dikkat gerektiren noktaları olgusal olarak işaretle ("Dikkat:" ile başlayan tek cümle olabilir): düşük uyum, \
belirli bir gün diliminde yoğunlaşan kaçırılan dozlar (ör. akşam dozları), eşik dışı ölçümler, kritik uyarılar.
- Hasta adı ya da kimlik bilgisi kullanma ("hasta" de).
- Türkçe, 3-5 cümle, en fazla 90 kelime, düz metin (başlık, madde işareti, markdown yok)."""

_UNIT_RX = re.compile(r"mg\s*/\s*dl", re.IGNORECASE)

CACHE_PREFIX = "narr:"
CACHE_TTL_SECONDS = 24 * 3600

_cache: dict[tuple, tuple[str, str]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()
    r = get_redis()
    if r is not None:
        try:
            for k in r.scan_iter(CACHE_PREFIX + "*"):
                r.delete(k)
        except Exception:
            logger.warning("Redis anlatı önbelleği temizlenemedi", exc_info=True)


def _redis_key(key: tuple) -> str:
    patient_id, day, period, fingerprint = key
    return f"{CACHE_PREFIX}{patient_id}:{day.isoformat()}:{period}:{fingerprint}"


def _cache_get(key: tuple) -> tuple[str | None, str | None] | None:
    r = get_redis()
    if r is not None:
        try:
            raw = r.get(_redis_key(key))
            if raw:
                narrative_text, source = json.loads(raw)
                return narrative_text, source
            return None
        except Exception:  # önbellek hatası isteği asla bozmaz
            logger.warning("Redis anlatı önbelleği okunamadı; süreç içi önbellek kullanılıyor", exc_info=True)
    with _lock:
        return _cache.get(key)


def _cache_set(key: tuple, value: tuple[str | None, str | None]) -> None:
    r = get_redis()
    if r is not None:
        try:
            r.setex(_redis_key(key), CACHE_TTL_SECONDS, json.dumps(list(value), ensure_ascii=False))
            return
        except Exception:
            logger.warning("Redis anlatı önbelleğine yazılamadı; süreç içi önbellek kullanılıyor", exc_info=True)
    with _lock:
        # yalnızca bugünün kayıtlarını tut (bellek sınırlı kalsın)
        for k in [k for k in _cache if k[1] != key[1]]:
            del _cache[k]
        _cache[key] = value


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def has_data(facts: dict) -> bool:
    return bool(facts["adherence"]["planned"] or facts["measurements"] or facts["alerts"]["total"])


def template_narrative(facts: dict) -> str | None:
    """Deterministik anlatı (LLM yokken veya başarısızken). Veri yoksa None."""
    if not has_data(facts):
        return None
    days = facts["period_days"]
    adh = facts["adherence"]
    parts: list[str] = []

    if adh["planned"]:
        pct = round((adh["score"] or 0) * 100)
        band = {"iyi": "iyi", "orta": "orta", "dusuk": "düşük"}.get(adh["band"], adh["band"])
        parts.append(f"Son {days} günde planlanan {adh['planned']} dozun {adh['taken']} tanesi alındı "
                     f"(uyum %{pct}, {band} bant).")
        slots = {k: v for k, v in (facts.get("missed_by_time_of_day") or {}).items() if v["missed"]}
        if slots:
            worst, w = max(slots.items(), key=lambda kv: (kv[1]["missed"], kv[1]["missed"] / kv[1]["planned"]))
            parts.append(f"Kaçırılan dozlar en çok {worst} saatlerinde ({w['missed']}/{w['planned']}).")
    else:
        parts.append(f"Son {days} günde planlı doz kaydı yok.")

    for m in facts["measurements"]:
        label, unit = MEASUREMENT_LABELS.get(m["type"], (m["type"], ""))
        if m.get("avg_diastolic") is not None:
            parts.append(f"{label}: {m['count']} ölçüm, ort. {_fmt(m['avg'])}/{_fmt(m['avg_diastolic'])} {unit} "
                         f"(sistolik aralık {_fmt(m['min'])}–{_fmt(m['max'])}).")
        else:
            parts.append(f"{label}: {m['count']} ölçüm, ort. {_fmt(m['avg'])} {unit} "
                         f"(aralık {_fmt(m['min'])}–{_fmt(m['max'])}).")

    al = facts["alerts"]
    if al["total"]:
        crit = f", {al['critical']} kritik" if al.get("critical") else ""
        parts.append(f"Dönemde {al['total']} uyarı oluştu{crit}.")
    else:
        parts.append("Dönemde uyarı oluşmadı.")

    flags = []
    if adh["planned"] and adh["score"] is not None and adh["score"] < 0.6:
        flags.append("düşük ilaç uyumu")
    if al.get("critical"):
        flags.append("kritik uyarı")
    if flags:
        parts.append(f"Dikkat: {', '.join(flags)}.")
    parts.append(DISCLAIMER)
    return " ".join(parts)


def _fingerprint(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _llm_narrative(db: Session, patient_id: int, facts: dict, client, settings: Settings) -> str:
    totals = UsageTotals(model=settings.model_name)
    try:
        resp = create_message(
            client or get_client(settings), settings,
            system=[{"type": "text", "text": NARRATIVE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": "Hasta verisi (JSON):\n" + json.dumps(
                facts, ensure_ascii=False, sort_keys=True, default=str)}],
        )
        totals.add(resp)
        if getattr(resp, "model", None):
            totals.model = resp.model
    finally:
        record_usage(db, patient_id, PURPOSE, totals)
        db.commit()
    if getattr(resp, "stop_reason", None) in ("refusal", "max_tokens"):
        raise LlmError(f"anlatı üretilemedi: {resp.stop_reason}")
    text = text_of(resp.content)
    if not text:
        raise LlmError("boş anlatı")
    # Kan şekeri birimi (mg/dL) doz ifadesi değildir; çıktı filtresi bu birim maskelenerek uygulanır
    if not gr.check_output(_UNIT_RX.sub(" ", text)).allowed:
        raise LlmError("anlatı çıktı guardrail'ine takıldı")
    return f"{text} {DISCLAIMER}"


def generate_narrative(db: Session, patient_id: int, facts: dict, client=None,
                       settings: Settings | None = None, now: datetime | None = None) -> tuple[str | None, str | None]:
    """(anlatı, kaynak) döndürür; kaynak 'llm' | 'template' | None (veri yok)."""
    s = settings or get_settings()
    if not has_data(facts):
        return None, None
    now = now or datetime.now(UTC)
    key = (patient_id, now.astimezone(ZoneInfo(s.timezone)).date(), facts["period_days"], _fingerprint(facts))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result: tuple[str | None, str | None] = (template_narrative(facts), "template")
    if s.llm_enabled or client is not None:
        with observe_llm(PURPOSE, s.model_name) as obs:
            decision = check_budget(db, patient_id, PURPOSE, s, now)
            if decision.allowed:
                try:
                    result = (_llm_narrative(db, patient_id, facts, client, s), "llm")
                except Exception as exc:
                    obs.outcome = "fallback"
                    logger.warning("LLM özet anlatısı başarısız (%s), şablon kullanılıyor", type(exc).__name__)
            else:
                obs.outcome = "budget_exceeded"
                logger.info("Özet anlatısı için LLM bütçesi uygun değil (%s)", decision.reason)

    _cache_set(key, result)
    return result
