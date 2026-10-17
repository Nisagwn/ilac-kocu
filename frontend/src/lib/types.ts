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
  adherence_prev_7d: number | null;
  adherence_trend: AdherenceTrend | null;
}

export type AdherenceTrend = "improving" | "stable" | "worsening";

/** GET /patients/{id}/insights — deterministik klinik özet (teşhis/tedavi önerisi içermez). */
export interface ClinicalInsights {
  period_days: number;
  adherence: { current_7d: number | null; previous_7d: number | null; delta: number | null; trend: AdherenceTrend | null };
  glucose: { target: string; count: number; time_in_range: number | null; below: number; above: number };
  blood_pressure: { target: string; count: number; at_target: number | null; below: number; above: number };
  measurement_frequency: {
    by_type: Partial<Record<MeasurementType, number>>;
    per_week: number;
    days_with_measurement: number;
    last_measured_at: string | null;
    days_since_last: number | null;
  };
  skip_reasons: { category: string; label: string; count: number }[];
  missed_by_time_of_day: Record<string, { planned: number; missed: number }>;
  highlights: string[];
}

export type ReviewStatus = "pending" | "approved" | "rejected";

export interface EducationItem {
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
  reviewed_at: string | null;
  review_note: string | null;
  reviewer_name: string | null;
  created_at: string;
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
  catalog_id: number | null;
  /** İlaç katalogda var mı; yoksa hekim kontrol etmeli */
  verified: boolean;
  schedules: Schedule[];
}

export interface MedicationIn {
  name: string;
  active_ingredient?: string | null;
  dose_text?: string | null;
  notes?: string | null;
  catalog_id?: number | null;
  schedules: ScheduleIn[];
}

export interface DrugSearchHit {
  id: number;
  generic_name: string;
  brand: string | null;
  display_name: string;
  brand_names: string[];
  indication_group: "diabetes" | "hypertension" | "other";
  pharm_class: string | null;
  source: "curated" | "openfda";
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
