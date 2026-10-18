"""Haftalık hasta özeti: deterministik istatistik + hekim için kısa anlatı (narrative).

Anlatı: LLM varsa ve bütçe uygunsa Claude ile, aksi halde deterministik şablonla üretilir
(bkz. app/services/llm/narrative.py). Veri varsa narrative asla None değildir.

`/summaries/{patient_id}` ve geriye dönük `/patients/{patient_id}/summary` aynı fonksiyonu kullanır.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert, Measurement, Severity
from app.services.adherence import adherence_band
from app.services.coach import get_adherence
from app.services.llm.narrative import generate_narrative
from app.services.llm.patient_data import missed_by_time_slot


def get_narrative_client():
    """FastAPI bağımlılığı: anlatı için LLM istemcisi. None = ayarlardaki anahtarla oluşturulur.
    Testlerde sahte istemciyle override edilir."""
    return None


def weekly_summary(db: Session, patient_id: int, days: int = 7, llm_client=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    adh = get_adherence(db, patient_id, days, now=now)
    stats = db.execute(
        select(Measurement.type, func.count(), func.avg(Measurement.value), func.min(Measurement.value),
               func.max(Measurement.value), func.avg(Measurement.value2))
        .where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
        .group_by(Measurement.type)
    ).all()
    alert_rows = db.execute(
        select(Alert.severity, Alert.kind, func.count())
        .where(Alert.patient_id == patient_id, Alert.created_at >= since)
        .group_by(Alert.severity, Alert.kind)
    ).all()
    alerts_total = sum(c for _, _, c in alert_rows)
    by_kind: dict[str, int] = {}
    for _, kind, c in alert_rows:
        by_kind[kind] = by_kind.get(kind, 0) + c

    adherence = {"score": adh.score, "planned": adh.planned, "taken": adh.taken, "band": adherence_band(adh.score)}
    measurements = [
        {"type": t.value, "count": c, "avg": round(a, 1), "min": mn, "max": mx,
         "avg_diastolic": round(a2, 1) if a2 else None}
        for t, c, a, mn, mx, a2 in stats
    ]
    missed_slots = missed_by_time_slot(db, patient_id, days, now)

    # LLM'e/şablona giden olgular: kimlik bilgisi yok, yalnızca sayısal özet
    facts = {
        "period_days": days,
        "adherence": adherence,
        "missed_by_time_of_day": missed_slots,
        "measurements": measurements,
        "alerts": {
            "total": alerts_total,
            "critical": sum(c for sev, _, c in alert_rows if sev == Severity.critical),
            "by_kind": dict(sorted(by_kind.items())),
        },
    }
    narrative, narrative_source = generate_narrative(db, patient_id, facts, client=llm_client, now=now)

    return {
        "period_days": days,
        "adherence": adherence,
        "measurements": measurements,
        "missed_by_time_of_day": missed_slots,
        "alerts_last_7d": alerts_total,
        "narrative": narrative,
        "narrative_source": narrative_source,
    }
