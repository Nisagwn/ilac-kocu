// Backend şemalarının (backend/app/schemas.py) TypeScript karşılıkları.

export type Role = "patient" | "clinician" | "caregiver";
export type DoseStatus = "taken" | "skipped";
export type MeasurementType = "glucose_fasting" | "glucose_postprandial" | "blood_pressure";
export type RiskLevel = "low" | "medium" | "high" | "emergency";
export type Severity = "info" | "warning" | "critical";
export type Band = "iyi" | "orta" | "dusuk" | "veri_yok";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  patient_id: number | null;
  clinician_id: number | null;
}

export interface Patient {
  id: number;
  full_name: string;
  birth_year: number | null;
  has_diabetes: boolean;
  has_hypertension: boolean;
  clinician_id: number | null;
}

export interface PatientSummary {
  id: number;
  full_name: string;
  adherence_7d: number | null;
  adherence_30d: number | null;
  band: Band;
  open_alerts: number;
  critical_alerts: number;
  last_measurement_at: string | null;
  /** Bu hafta − geçen hafta uyum farkı (yüzde puan); veri yoksa null. */
  adherence_trend_delta?: number | null;
  adherence_trend_direction?: TrendDirection | null;
}

export interface Schedule {
  id: number;
  time_of_day: string; // "08:00:00"
  days_of_week: string; // "1234567"
  start_date: string;
}

export interface ScheduleIn {
  time_of_day: string;
  days_of_week?: string;
}

export interface Medication {
  id: number;
  patient_id: number;
  name: string;
  active_ingredient: string | null;
  dose_text: string | null;
  notes: string | null;
  active: boolean;
  schedules: Schedule[];
}

export interface MedicationIn {
  name: string;
  active_ingredient?: string | null;
  dose_text?: string | null;
  notes?: string | null;
  schedules: ScheduleIn[];
}

export interface TodayDose {
  schedule_id: number;
  medication_id: number;
  medication_name: string;
  dose_text: string | null;
  time_of_day: string;
  status: DoseStatus | null;
}

export interface DoseLog {
  id: number;
  schedule_id: number;
  scheduled_date: string;
  status: DoseStatus;
  reason: string | null;
  logged_at: string;
}

export interface Adherence {
  start: string;
  end: string;
  planned: number;
  taken: number;
  skipped: number;
  missing: number;
  score: number | null;
  percent: number | null;
  band: Band;
  by_medication: Record<string, { planned: number; taken: number; score: number | null }>;
  missed_dates: string[];
}

export interface Measurement {
  id: number;
  type: MeasurementType;
  value: number;
  value2: number | null;
  unit: string;
  note: string | null;
  measured_at: string;
}

export interface MeasurementIn {
  type: MeasurementType;
  value: number;
  value2?: number | null;
  note?: string | null;
  measured_at?: string | null;
}

export interface Alert {
  id: number;
  patient_id: number;
  severity: Severity;
  kind: string;
  message: string;
  created_at: string;
  acknowledged_at: string | null;
}

export interface MeasurementCreated extends Measurement {
  alert: Alert | null;
}

export interface Source {
  title: string;
  url: string | null;
}

export interface CoachReply {
  message: string;
  risk_level: RiskLevel;
  escalate_to_clinician: boolean;
  sources: Source[];
  guardrail_action: string | null;
}

export type CoachJobStatus = "queued" | "running" | "done" | "failed";

// POST /coach/jobs (200: girdi guardrail'i anında yanıtladı, job_id null; 202: kuyrukta) ve GET /coach/jobs/{id}
export interface CoachJob {
  job_id: string | null;
  status: CoachJobStatus;
  reply: CoachReply | null;
  error: string | null;
}

export interface CoachMessage {
  id: number;
  sender: "patient" | "coach";
  content: string;
  risk_level: RiskLevel | null;
  escalate_to_clinician: boolean;
  sources: Source[] | null;
  created_at: string;
}

export interface WeeklySummary {
  period_days: number;
  adherence: { score: number | null; planned: number; taken: number; band: Band };
  measurements: {
    type: MeasurementType;
    count: number;
    avg: number;
    min: number;
    max: number;
    avg_diastolic: number | null;
  }[];
  alerts_last_7d: number;
  narrative: string | null;
}

export interface DrugLabel {
  [key: string]: unknown;
}

// --- Klinik içgörüler (GET /patients/{id}/insights) ---------------------------------------
export type TrendDirection = "up" | "down" | "flat";

export interface AdherenceTrend {
  current_start: string;
  current_end: string;
  previous_start: string;
  previous_end: string;
  current_score: number | null;
  previous_score: number | null;
  current_percent: number | null;
  previous_percent: number | null;
  delta_points: number | null;
  direction: TrendDirection | null;
}

export interface TargetStats {
  count: number;
  in_target: number;
  in_target_ratio: number | null;
  avg: number | null;
  avg_diastolic: number | null;
}

export interface TargetWindows {
  target: string;
  window_30d: TargetStats;
  window_7d: TargetStats;
}

export type SkipCategoryKey = "yan_etki" | "ilac_bitti" | "unuttum" | "iyi_hissetti" | "diger";

export interface ClinicalInsights {
  patient_id: number;
  as_of: string;
  adherence_trend: AdherenceTrend;
  targets: Record<MeasurementType, TargetWindows>;
  skip_reasons: {
    window_days: number;
    total: number;
    categories: { key: SkipCategoryKey; label: string; count: number; percent: number }[];
  };
  measurement_frequency: {
    window_days: number;
    days_with_measurement: number;
    min_expected_days: number;
    low_data: boolean;
  };
  disclaimer: string;
}

// --- Eğitim içeriği onayı (/education) -------------------------------------------------------
export type ReviewStatus = "pending" | "approved" | "rejected";

export interface EducationContent {
  id: number;
  title: string;
  body: string;
  source_name: string;
  source_url: string | null;
  tags: string | null;
  kind: "curated" | "openfda";
  drug_key: string | null;
  reviewed: boolean;
  review_status: ReviewStatus;
  reviewed_by: number | null;
  reviewed_at: string | null;
  embedding_model: string | null;
  created_at: string;
}

// --- İlaç kataloğu (GET /drugs/search; medications.catalog_id / verified) ---
export interface DrugSearchResult {
  id: number;
  display_name: string;
  generic_name: string;
  /** Sorguyla eşleşen ticari ad; etken maddeyle eşleştiyse null. */
  matched_brand: string | null;
  tr_brand_names: string[];
  pharm_class: string | null;
  source: "tr_curated" | "openfda";
  openfda_available: boolean;
}

/** Backend MedicationOut / MedicationIn katalog alanları (Medication / MedicationIn ile kesişim olarak). */
export interface MedicationCatalogFields {
  catalog_id?: number | null;
  verified?: boolean;
}

// Cursor sayfalı yeni /v1 uçlarının yanıtı (ör. /v1/measurements, /v1/doses)
export type Page<T> = { items: T[]; next_cursor: string | null };
