import { ApiError } from "@/lib/api";
import type { Band, MeasurementType } from "@/lib/types";

/** Tarayıcının yerel saatine göre YYYY-MM-DD. (toISOString UTC'ye çevirdiği için kullanılmaz.) */
export function localDateStr(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** "08:00:00" → "08:00" */
export function hhmm(time: string): string {
  return time.slice(0, 5);
}

export function errorMessage(e: unknown, fallback = "Beklenmeyen bir hata oluştu"): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

export function errorStatus(e: unknown): number | null {
  return e instanceof ApiError ? e.status : null;
}

export const BAND_LABEL: Record<Band, string> = {
  iyi: "İyi",
  orta: "Orta",
  dusuk: "Düşük",
  veri_yok: "Veri yok",
};

export const BAND_CLASS: Record<Band, string> = {
  iyi: "bg-emerald-100 text-emerald-800",
  orta: "bg-amber-100 text-amber-800",
  dusuk: "bg-red-100 text-red-800",
  veri_yok: "bg-muted text-muted-foreground",
};

export const MEASUREMENT_LABEL: Record<MeasurementType, string> = {
  glucose_fasting: "Açlık kan şekeri",
  glucose_postprandial: "Tokluk kan şekeri",
  blood_pressure: "Tansiyon",
};

export const MEASUREMENT_UNIT: Record<MeasurementType, string> = {
  glucose_fasting: "mg/dL",
  glucose_postprandial: "mg/dL",
  blood_pressure: "mmHg",
};

/** ISO zaman damgası → "3 Eki 08:15" */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("tr-TR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatShortDate(ms: number): string {
  return new Date(ms).toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
}

/** ISO 8601 haftanın günleri (backend days_of_week: "1"=Pazartesi … "7"=Pazar). */
export const WEEKDAYS: { value: string; short: string; long: string }[] = [
  { value: "1", short: "Pzt", long: "Pazartesi" },
  { value: "2", short: "Sal", long: "Salı" },
  { value: "3", short: "Çar", long: "Çarşamba" },
  { value: "4", short: "Per", long: "Perşembe" },
  { value: "5", short: "Cum", long: "Cuma" },
  { value: "6", short: "Cmt", long: "Cumartesi" },
  { value: "7", short: "Paz", long: "Pazar" },
];

export function describeDays(days: string): string {
  if (days.length === 7) return "Her gün";
  return WEEKDAYS.filter((d) => days.includes(d.value))
    .map((d) => d.short)
    .join(", ");
}
