from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import DoseStatus, MeasurementType, Role, Severity


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    role: Role = Role.patient
    # hasta alanları
    birth_year: int | None = Field(default=None, ge=1900, le=2026)
    has_diabetes: bool = False
    has_hypertension: bool = False
    consent: bool = False
    # hekim alanları
    title: str | None = None
    institution: str | None = None

    @model_validator(mode="after")
    def patient_needs_consent(self):
        if self.role == Role.patient and not self.consent:
            raise ValueError("Hasta kaydı için veri işleme onayı gereklidir")
        return self


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role


class UserOut(ORM):
    id: int
    email: str  # çıktı: doğrulama girişte yapılır (.local demo alan adları EmailStr ile reddedilir)
    full_name: str
    role: Role
    patient_id: int | None = None
    clinician_id: int | None = None


# ---------- Patients ----------
class PatientOut(ORM):
    id: int
    full_name: str
    birth_year: int | None
    has_diabetes: bool
    has_hypertension: bool
    clinician_id: int | None


class PatientUpdate(BaseModel):
    birth_year: int | None = Field(default=None, ge=1900, le=2026)
    has_diabetes: bool | None = None
    has_hypertension: bool | None = None


class AssignClinicianIn(BaseModel):
    clinician_email: EmailStr


class AddCaregiverIn(BaseModel):
    caregiver_email: EmailStr


class PatientSummaryOut(BaseModel):
    """Hekim dashboard'u hasta listesi satırı."""

    id: int
    full_name: str
    adherence_7d: float | None
    adherence_30d: float | None
    band: str
    open_alerts: int
    critical_alerts: int
    last_measurement_at: datetime | None
    # Bu hafta vs geçen hafta uyum farkı (yüzde puan) ve yönü: up | down | flat; veri yoksa None
    adherence_trend_delta: int | None = None
    adherence_trend_direction: str | None = None


# ---------- Medications ----------
class ScheduleIn(BaseModel):
    time_of_day: time
    days_of_week: str = Field(default="1234567", pattern=r"^[1-7]{1,7}$")

    @field_validator("days_of_week")
    @classmethod
    def unique_days(cls, v: str) -> str:
        return "".join(sorted(set(v)))


class ScheduleOut(ORM):
    id: int
    time_of_day: time
    days_of_week: str
    start_date: date


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    active_ingredient: str | None = None
    dose_text: str | None = Field(default=None, max_length=120, description="Hekimin yazdığı doz, ör. '1000 mg'")
    notes: str | None = None
    schedules: list[ScheduleIn] = Field(default_factory=list)
    catalog_id: int | None = Field(
        default=None,
        description="İlaç kataloğundan seçildiyse katalog id'si (GET /drugs/search). Verilirse etken madde "
        "katalogdan gelir ve ilaç doğrulanmış (verified) olur; verilmezse ad katalogla birebir eşleşmedikçe "
        "doğrulanmamış kaydedilir.",
    )


class MedicationUpdate(BaseModel):
    name: str | None = None
    catalog_id: int | None = None  # gönderilip null ise katalog bağı kaldırılır (doğrulanmamış)
    active_ingredient: str | None = None
    dose_text: str | None = None
    notes: str | None = None
    active: bool | None = None
    schedules: list[ScheduleIn] | None = None


class MedicationOut(ORM):
    id: int
    patient_id: int
    name: str
    active_ingredient: str | None
    dose_text: str | None
    notes: str | None
    active: bool
    catalog_id: int | None = None
    verified: bool = False
    schedules: list[ScheduleOut]


# ---------- Doses ----------
class DoseLogIn(BaseModel):
    schedule_id: int
    scheduled_date: date
    status: DoseStatus
    reason: str | None = Field(default=None, max_length=255)


class DoseLogOut(ORM):
    id: int
    schedule_id: int
    scheduled_date: date
    status: DoseStatus
    reason: str | None
    logged_at: datetime


class TodayDoseOut(BaseModel):
    schedule_id: int
    medication_id: int
    medication_name: str
    dose_text: str | None
    time_of_day: time
    status: DoseStatus | None


class AdherenceOut(BaseModel):
    start: date
    end: date
    planned: int
    taken: int
    skipped: int
    missing: int
    score: float | None
    percent: int | None
    band: str
    by_medication: dict[int, dict]
    missed_dates: list[str]


# ---------- Measurements ----------
class MeasurementIn(BaseModel):
    type: MeasurementType
    value: float = Field(gt=0, lt=1000)
    value2: float | None = Field(default=None, gt=0, lt=300)
    note: str | None = Field(default=None, max_length=255)
    measured_at: datetime | None = None

    @model_validator(mode="after")
    def bp_needs_diastolic(self):
        if self.type == MeasurementType.blood_pressure and self.value2 is None:
            raise ValueError("Tansiyon ölçümü için diyastolik değer (value2) gereklidir")
        if self.type != MeasurementType.blood_pressure and self.value2 is not None:
            raise ValueError("value2 yalnızca tansiyon ölçümünde kullanılır")
        return self


class MeasurementOut(ORM):
    id: int
    type: MeasurementType
    value: float
    value2: float | None
    unit: str
    note: str | None
    measured_at: datetime


class MeasurementCreated(MeasurementOut):
    alert: "AlertOut | None" = None


# ---------- Alerts ----------
class AlertOut(ORM):
    id: int
    patient_id: int
    severity: Severity
    kind: str
    message: str
    created_at: datetime
    acknowledged_at: datetime | None


# ---------- Coach ----------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class CoachMessageOut(ORM):
    id: int
    sender: str
    content: str
    risk_level: str | None
    escalate_to_clinician: bool
    sources: list | None
    created_at: datetime


MeasurementCreated.model_rebuild()


# ---------- Web push ----------
class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)


class VapidKeyOut(BaseModel):
    public_key: str


class PushTestOut(BaseModel):
    sent: int


# ---------- İlaç kataloğu ----------
class DrugSearchOut(BaseModel):
    id: int
    display_name: str
    generic_name: str
    matched_brand: str | None = Field(description="Sorguyla eşleşen ticari ad (etken maddeyle eşleştiyse null)")
    tr_brand_names: list[str]
    pharm_class: str | None
    source: str  # tr_curated | openfda
    openfda_available: bool


# ---------- Klinik içgörüler (hekim dashboard'u) ----------
class AdherenceTrendOut(BaseModel):
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    current_score: float | None
    previous_score: float | None
    current_percent: int | None
    previous_percent: int | None
    delta_points: int | None
    direction: str | None  # up | down | flat


class TargetStatsOut(BaseModel):
    count: int
    in_target: int
    in_target_ratio: float | None
    avg: float | None
    avg_diastolic: float | None = None


class TargetWindowsOut(BaseModel):
    target: str
    window_30d: TargetStatsOut
    window_7d: TargetStatsOut


class SkipCategoryOut(BaseModel):
    key: str
    label: str
    count: int
    percent: int


class SkipReasonsOut(BaseModel):
    window_days: int
    total: int
    categories: list[SkipCategoryOut]


class MeasurementFrequencyOut(BaseModel):
    window_days: int
    days_with_measurement: int
    min_expected_days: int
    low_data: bool


class ClinicalInsightsOut(BaseModel):
    """Hekim değerlendirmesi için veri özeti; teşhis/tedavi önerisi içermez."""

    patient_id: int
    as_of: date
    adherence_trend: AdherenceTrendOut
    targets: dict[MeasurementType, TargetWindowsOut]
    skip_reasons: SkipReasonsOut
    measurement_frequency: MeasurementFrequencyOut
    disclaimer: str
