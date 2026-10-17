"""SQLAlchemy modelleri. ER diyagramı: docs/er-diyagrami.md"""

import enum
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.services.rag.vector_type import EMBEDDING_DIM, EmbeddingVector


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(str, enum.Enum):
    patient = "patient"
    clinician = "clinician"
    caregiver = "caregiver"


class DoseStatus(str, enum.Enum):
    taken = "taken"
    skipped = "skipped"


class MeasurementType(str, enum.Enum):
    glucose_fasting = "glucose_fasting"
    glucose_postprandial = "glucose_postprandial"
    blood_pressure = "blood_pressure"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    emergency = "emergency"


class Sender(str, enum.Enum):
    patient = "patient"
    coach = "coach"


class Severity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped["Patient | None"] = relationship(back_populates="user", foreign_keys="Patient.user_id")
    clinician: Mapped["Clinician | None"] = relationship(back_populates="user")


class Clinician(Base):
    __tablename__ = "clinicians"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str | None] = mapped_column(String(80))
    institution: Mapped[str | None] = mapped_column(String(160))

    user: Mapped[User] = relationship(back_populates="clinician")
    patients: Mapped[list["Patient"]] = relationship(back_populates="clinician")


class PatientCaregiver(Base):
    __tablename__ = "patient_caregivers"

    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), primary_key=True)
    caregiver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    clinician_id: Mapped[int | None] = mapped_column(ForeignKey("clinicians.id", ondelete="SET NULL"), index=True)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    has_diabetes: Mapped[bool] = mapped_column(Boolean, default=False)
    has_hypertension: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="patient", foreign_keys=[user_id])
    clinician: Mapped[Clinician | None] = relationship(back_populates="patients")
    medications: Mapped[list["Medication"]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    active_ingredient: Mapped[str | None] = mapped_column(String(120))
    dose_text: Mapped[str | None] = mapped_column(String(120))  # hekimin yazdığı haliyle, ör. "1000 mg"
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # İlaç kataloğundan seçildiyse (ya da ad katalogla birebir eşleştiyse) verified=True; değilse hekim kontrol eder
    catalog_id: Mapped[int | None] = mapped_column(ForeignKey("drug_catalog.id", ondelete="SET NULL"), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    patient: Mapped[Patient] = relationship(back_populates="medications")
    catalog: Mapped["DrugCatalog | None"] = relationship()
    schedules: Mapped[list["MedicationSchedule"]] = relationship(
        back_populates="medication", cascade="all, delete-orphan"
    )


class DrugCatalog(Base):
    """İlaç kataloğu: etken madde başına bir satır (openFDA generic_name, küçük harf, tuz eki atılmış).

    Kaynak: kürate TR ticari ad listesi (app/data/drug_catalog_tr.yaml) ve openFDA `count` senkronu
    (app/services/drug_catalog.py). `search_text`: Türkçe karakter duyarsız arama için normalize metin.
    """

    __tablename__ = "drug_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    generic_name: Mapped[str] = mapped_column(String(160), unique=True)
    brand_names: Mapped[list] = mapped_column(JSON, default=list)  # TR ticari adlar (görüntülenen haliyle)
    indication_group: Mapped[str] = mapped_column(String(20), default="other")  # diabetes | hypertension | other
    pharm_class: Mapped[str | None] = mapped_column(String(160))  # openFDA EPC sınıfı
    source: Mapped[str] = mapped_column(String(20), default="curated")  # curated | openfda
    label_count: Mapped[int | None] = mapped_column(Integer)  # openFDA'daki etiket sayısı (senkrondan)
    search_text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MedicationSchedule(Base):
    __tablename__ = "medication_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id", ondelete="CASCADE"), index=True)
    time_of_day: Mapped[time] = mapped_column(Time)
    # ISO hafta günleri: "1234567" = her gün, "135" = Pzt/Çar/Cum
    days_of_week: Mapped[str] = mapped_column(String(7), default="1234567")
    start_date: Mapped[date] = mapped_column(Date, default=lambda: utcnow().date())

    medication: Mapped[Medication] = relationship(back_populates="schedules")


class DoseLog(Base):
    __tablename__ = "dose_logs"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_date", name="uq_dose_per_schedule_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("medication_schedules.id", ondelete="CASCADE"))
    scheduled_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[DoseStatus] = mapped_column(_enum(DoseStatus, "dose_status"))
    reason: Mapped[str | None] = mapped_column(String(255))
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReminderLog(Base):
    """Gönderilmiş doz hatırlatmaları: bir planlı doz için gün başına en fazla bir bildirim."""

    __tablename__ = "reminder_logs"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_date", name="uq_reminder_per_schedule_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("medication_schedules.id", ondelete="CASCADE"))
    scheduled_date: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PushSubscription(Base):
    """Tarayıcı web push aboneliği (Web Push Protocol; endpoint + şifreleme anahtarları)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    type: Mapped[MeasurementType] = mapped_column(_enum(MeasurementType, "measurement_type"))
    value: Mapped[float] = mapped_column(Float)  # kan şekeri mg/dL veya sistolik mmHg
    value2: Mapped[float | None] = mapped_column(Float)  # diyastolik mmHg
    unit: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(255))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CoachMessage(Base):
    __tablename__ = "coach_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    sender: Mapped[Sender] = mapped_column(_enum(Sender, "message_sender"))
    content: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[RiskLevel | None] = mapped_column(_enum(RiskLevel, "risk_level"))
    escalate_to_clinician: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[list | None] = mapped_column(JSON)
    guardrail_action: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "alert_severity"))
    kind: Mapped[str] = mapped_column(String(40))  # emergency_symptom, out_of_range, low_adherence...
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EducationContent(Base):
    __tablename__ = "education_contents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(160))
    source_url: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # M4 RAG (migration 0003_rag): PostgreSQL'de pgvector vector(384), SQLite'ta JSON
    kind: Mapped[str] = mapped_column(String(20), default="curated")  # curated | openfda
    drug_key: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)  # openfda: etken madde
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)  # = review_status == "approved" (geriye dönük)
    # Hekim onayı (migration 0009): pending | approved | rejected. Reddedilen içerik RAG'de asla kullanılmaz;
    # içerik metni değişince (YAML ya da openFDA etiketi güncellenince) yeniden "pending" olur.
    review_status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector(EMBEDDING_DIM), deferred=True)
    embedding_model: Mapped[str | None] = mapped_column(String(80))


REVIEW_STATUSES = ("pending", "approved", "rejected")


def set_review(row: EducationContent, status: str, user_id: int | None = None, note: str | None = None) -> None:
    """Onay durumunu ve geriye dönük `reviewed` alanını birlikte günceller."""
    if status not in REVIEW_STATUSES:
        raise ValueError(status)
    row.review_status = status
    row.reviewed = status == "approved"
    row.reviewed_by_user_id = user_id
    row.reviewed_at = utcnow() if status != "pending" else None
    row.review_note = note


class LlmUsage(Base):
    """LLM çağrılarının token/maliyet kaydı (maliyet limiti için). Mesaj içeriği tutulmaz.

    Hasta silinse de kayıt anonim toplam olarak kalır (patient_id NULL olur).
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # coach | summary
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CoachJob(Base):
    """Asenkron koç işi (POST /coach/jobs). Kuyruk işçisi (app/worker.py) LLM yanıtını üretir.

    status: queued -> running -> done | failed. Hasta mesajı koç sohbetine iş oluşturulurken kaydedilir;
    `message` yalnızca işlenene kadar tutulur, iş bitince temizlenir (gizlilik).
    """

    __tablename__ = "coach_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)
    reply: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # DLQ (migration 0010): tüm denemeler tükenince iş "ölü mektup" olur; mesaj DLQ_RETENTION_DAYS boyunca
    # yeniden deneme için tutulur. last_error yalnızca hata türüdür (içerik yok).
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(80))


class WeeklySummary(Base):
    """Zamanlanmış haftalık özet (Pazartesi 07:00, lider işçi; app/services/weekly_reports.py).

    `payload`: services/summary.weekly_summary çıktısı (sayısal özet + anlatı). Hasta başına dönem başına bir satır.
    """

    __tablename__ = "weekly_summaries"
    __table_args__ = (UniqueConstraint("patient_id", "period_start", name="uq_weekly_summary_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)
    narrative_source: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    """Yenileme token'ı. Token'ın kendisi tutulmaz, yalnızca SHA-256 özeti saklanır.

    Her kullanımda döndürülür (eskisi iptal edilir, `replaced_by` yenisini gösterir). İptal edilmiş bir
    token yeniden kullanılırsa çalınma varsayılır ve kullanıcının tüm yenileme token'ları iptal edilir.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    replaced_by: Mapped[int | None] = mapped_column(Integer)  # döndürmede yerine geçen token'ın id'si


class AccessLog(Base):
    """KVKK hesap verebilirlik: hekim/bakım veren hasta verisine eriştiğinde bir satır.

    Yalnızca üst veri tutulur (kim, hangi hasta, hangi uç nokta, ne zaman); sağlık değeri asla yazılmaz.
    Hasta silinirse kayıtları da silinir; eriştiren hesap silinirse kayıt kalır (actor_user_id NULL olur).
    """

    __tablename__ = "access_logs"
    __table_args__ = (Index("ix_access_logs_patient_created", "patient_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(20))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))  # read | write
    path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyKey(Base):
    """Idempotency-Key ile gelen yazma isteklerinin sonucu (app/services/idempotency.py).

    Aynı kullanıcı aynı anahtarla aynı isteği tekrarlarsa işlem yeniden yapılmaz, saklanan yanıt döner.
    `status_code` NULL: istek hâlâ işleniyor. Yanıt gövdesi sağlık verisi içerebilir: kayıtlar
    IDEMPOTENCY_TTL_HOURS sonra silinir, kullanıcı silinince de silinir.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))  # yöntem + yol + gövdenin SHA-256 özeti
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    response_headers: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
