"""Maliyet limiti: LLM çağrısından ÖNCE bütçe kontrolü, SONRA kullanım kaydı.

Limitler (config):
- llm_daily_patient_requests / llm_daily_patient_tokens: hasta başına, yerel takvim günü (koç)
- llm_daily_summary_requests: hasta başına günlük LLM özet üretimi
- llm_monthly_budget_usd: tüm kullanım için takvim ayı (UTC) tavanı
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import LlmUsage
from app.services.llm.pricing import cost_usd


@dataclass
class UsageTotals:
    """Bir istek boyunca (tool-use döngüsünün tüm turları) toplanan token sayıları."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    reported_cost: float = 0.0  # sağlayıcının bildirdiği gerçek maliyet (OpenRouter usage.cost)
    unreported_calls: int = 0  # maliyet bildirilmeyen çağrılar: bunlar fiyat tablosundan hesaplanır

    def add(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.calls += 1
        if usage is None:
            return
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = getattr(usage, "cost", None)
        if isinstance(cost, int | float):
            self.reported_cost += float(cost)
        else:
            self.unreported_calls += 1

    @property
    def cost_usd(self) -> float:
        # Tüm çağrılar maliyet bildirdiyse gerçek tutar; aksi halde fiyat tablosu (bilinmeyen model en pahalı)
        if self.calls and self.unreported_calls == 0:
            return round(self.reported_cost, 6)
        return cost_usd(self.model, self.input_tokens, self.output_tokens, self.cache_read_tokens,
                        self.cache_write_tokens)


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None  # daily_requests | daily_tokens | daily_summaries | monthly_budget


def _local_day_start_utc(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return datetime.combine(local.date(), time.min, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _month_start_utc(now: datetime) -> datetime:
    n = now.astimezone(UTC)
    return datetime(n.year, n.month, 1, tzinfo=UTC)


def monthly_spend(db: Session, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    return float(
        db.scalar(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
            LlmUsage.created_at >= _month_start_utc(now))) or 0.0
    )


def check_budget(db: Session, patient_id: int | None, purpose: str, settings: Settings | None = None,
                 now: datetime | None = None) -> BudgetDecision:
    s = settings or get_settings()
    now = now or datetime.now(UTC)

    if monthly_spend(db, now) >= s.llm_monthly_budget_usd:
        return BudgetDecision(False, "monthly_budget")
    if patient_id is None:
        return BudgetDecision(True)

    day_start = _local_day_start_utc(now, s.timezone)
    count, tokens = db.execute(
        select(func.count(), func.coalesce(
            func.sum(LlmUsage.input_tokens + LlmUsage.cache_write_tokens + LlmUsage.output_tokens), 0))
        .where(LlmUsage.patient_id == patient_id, LlmUsage.purpose == purpose, LlmUsage.created_at >= day_start)
    ).one()

    if purpose == "summary":
        if count >= s.llm_daily_summary_requests:
            return BudgetDecision(False, "daily_summaries")
        return BudgetDecision(True)
    if count >= s.llm_daily_patient_requests:
        return BudgetDecision(False, "daily_requests")
    if tokens >= s.llm_daily_patient_tokens:
        return BudgetDecision(False, "daily_tokens")
    return BudgetDecision(True)


def record_usage(db: Session, patient_id: int | None, purpose: str, totals: UsageTotals) -> LlmUsage | None:
    """Kullanımı oturuma ekler (commit çağıran tarafta). Hiç çağrı yapılmadıysa kayıt yok."""
    if totals.calls == 0:
        return None
    row = LlmUsage(
        patient_id=patient_id, purpose=purpose, model=totals.model,
        input_tokens=totals.input_tokens, output_tokens=totals.output_tokens,
        cache_read_tokens=totals.cache_read_tokens, cache_write_tokens=totals.cache_write_tokens,
        cost_usd=totals.cost_usd,
    )
    db.add(row)
    return row
