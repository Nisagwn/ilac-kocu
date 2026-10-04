// Tarayıcı tarafı API istemcisi. Tüm istekler Next.js'in /api/proxy rotasından geçer;
// JWT httpOnly çerezde durur, JavaScript'e hiç açılmaz (XSS ile token çalınamaz).

import type {
  Adherence,
  Alert,
  CoachJob,
  CoachMessage,
  CoachReply,
  DoseLog,
  DoseStatus,
  DrugLabel,
  Measurement,
  MeasurementCreated,
  MeasurementIn,
  MeasurementType,
  Medication,
  MedicationIn,
  Patient,
  PatientSummary,
  TodayDose,
  User,
  WeeklySummary,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
  return "Beklenmeyen bir hata oluştu";
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/proxy${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/giris";
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detailToMessage(body.detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  me: () => request<User>("/auth/me"),

  patients: () => request<PatientSummary[]>("/patients"),
  patient: (id: number) => request<Patient>(`/patients/${id}`),
  adherence: (id: number, days = 7) => request<Adherence>(`/patients/${id}/adherence?days=${days}`),
  summary: (id: number) => request<WeeklySummary>(`/patients/${id}/summary`),

  medications: (id: number, includeInactive = false) =>
    request<Medication[]>(`/patients/${id}/medications${includeInactive ? "?include_inactive=true" : ""}`),
  createMedication: (id: number, data: MedicationIn) =>
    request<Medication>(`/patients/${id}/medications`, { method: "POST", body: json(data) }),
  updateMedication: (id: number, medId: number, data: Partial<MedicationIn> & { active?: boolean }) =>
    request<Medication>(`/patients/${id}/medications/${medId}`, { method: "PATCH", body: json(data) }),
  deactivateMedication: (id: number, medId: number) =>
    request<void>(`/patients/${id}/medications/${medId}`, { method: "DELETE" }),

  todayDoses: (id: number, day?: string) =>
    request<TodayDose[]>(`/patients/${id}/doses/today${day ? `?day=${day}` : ""}`),
  logDose: (id: number, data: { schedule_id: number; scheduled_date: string; status: DoseStatus; reason?: string }) =>
    request<DoseLog>(`/patients/${id}/doses`, { method: "PUT", body: json(data) }),
  doses: (id: number, days = 30) => request<DoseLog[]>(`/patients/${id}/doses?days=${days}`),

  measurements: (id: number, days = 30, type?: MeasurementType) =>
    request<Measurement[]>(`/patients/${id}/measurements?days=${days}${type ? `&type=${type}` : ""}`),
  createMeasurement: (id: number, data: MeasurementIn) =>
    request<MeasurementCreated>(`/patients/${id}/measurements`, { method: "POST", body: json(data) }),
  deleteMeasurement: (id: number, mId: number) =>
    request<void>(`/patients/${id}/measurements/${mId}`, { method: "DELETE" }),

  patientAlerts: (id: number, openOnly = false) =>
    request<Alert[]>(`/patients/${id}/alerts?open_only=${openOnly}`),
  openAlerts: () => request<Alert[]>("/alerts"),
  ackAlert: (alertId: number) => request<Alert>(`/alerts/${alertId}/ack`, { method: "POST" }),

  chat: (message: string) => request<CoachReply>("/coach/chat", { method: "POST", body: json({ message }) }),
  createCoachJob: (message: string) => request<CoachJob>("/coach/jobs", { method: "POST", body: json({ message }) }),
  coachJob: (jobId: string) => request<CoachJob>(`/coach/jobs/${encodeURIComponent(jobId)}`),
  chatHistory: (limit = 50) => request<CoachMessage[]>(`/coach/history?limit=${limit}`),

  drugLabel: (name: string) => request<DrugLabel>(`/drugs/label?name=${encodeURIComponent(name)}`),
};
