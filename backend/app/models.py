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
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


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

    patient: Mapped[Patient] = relationship(back_populates="medications")
    schedules: Mapped[list["MedicationSchedule"]] = relationship(
        back_populates="medication", cascade="all, delete-orphan"
    )


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
    # embedding (pgvector) M4'te ayrı migration ile eklenecek
