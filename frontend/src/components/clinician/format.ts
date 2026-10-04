// Hekim / yakın ekranlarında ortak biçimlendirme ve klinik eşik yardımcıları.

import { ApiError } from "@/lib/api";
import type { Band, Measurement, MeasurementType, Severity } from "@/lib/types";

export const BAND_LABEL: Record<Band, string> = {
  iyi: "İyi",
  orta: "Orta",
  dusuk: "Düşük",
  veri_yok: "Veri yok",
};

export const BAND_CLASS: Record<Band, string> = {
  iyi: "border-emerald-200 bg-emerald-50 text-emerald-800",
  orta: "border-amber-200 bg-amber-50 text-amber-800",
  dusuk: "border-red-200 bg-red-50 text-red-800",
  veri_yok: "border-border bg-muted text-muted-foreground",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Kritik",
  warning: "Uyarı",
  info: "Bilgi",
};

export const MEASUREMENT_LABEL: Record<MeasurementType, string> = {
  glucose_fasting: "Açlık kan şekeri",
  glucose_postprandial: "Tokluk kan şekeri",
  blood_pressure: "Tansiyon",
};

/** Backend'deki adherence_band ile aynı eşikler (skor 0..1). */
export function bandOf(score: number | null): Band {
  if (score === null) return "veri_yok";
  if (score >= 0.8) return "iyi";
  if (score >= 0.6) return "orta";
  return "dusuk";
}

/** Skor 0..1 → "%85". */
export function pct(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : `%${Math.round(score * 100)}`;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return "Kayıt bulunamadı veya bu veriye erişim yetkiniz yok.";
    return e.message;
  }
  if (e instanceof Error) return e.message;
  return "Beklenmeyen bir hata oluştu";
}

const rtf = new Intl.RelativeTimeFormat("tr", { numeric: "auto" });

export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "Ölçüm yok";
  const diffSec = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 60) return rtf.format(diffSec, "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diffSec / 86400), "day");
  return rtf.format(Math.round(diffSec / (86400 * 30)), "month");
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function formatShortDate(value: number | string): string {
  return new Date(value).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit" });
}

/** "2026-10-03" gibi yerel tarih anahtarı (saat dilimi kaymasını önlemek için elle). */
export function dateKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function parseDateKey(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// --- Klinik hedef aralıkları (görselleştirme için; backend uyarı eşikleri services/thresholds.py'de) ---

export const GLUCOSE_LOW = 70;
export const GLUCOSE_HIGH = 180;
export const SYSTOLIC_TARGET = 130;
export const DIASTOLIC_TARGET = 80;

export function isGlucoseOutOfRange(v: number): boolean {
  return v < GLUCOSE_LOW || v > GLUCOSE_HIGH;
}

export function isBpAboveTarget(sys: number, dia: number | null): boolean {
  return sys >= SYSTOLIC_TARGET || (dia !== null && dia >= DIASTOLIC_TARGET) || sys < 90;
}

export function isOutOfRange(m: Measurement): boolean {
  return m.type === "blood_pressure" ? isBpAboveTarget(m.value, m.value2) : isGlucoseOutOfRange(m.value);
}

export interface PeriodTrend {
  current: number | null;
  previous: number | null;
  currentCount: number;
  previousCount: number;
}

/** Son 7 gün ortalaması ile önceki 7 gün (8–14. gün) ortalamasını karşılaştırır. */
export function periodTrend(
  items: Measurement[],
  type: MeasurementType,
  pick: (m: Measurement) => number | null = (m) => m.value,
  now: number = Date.now(),
): PeriodTrend {
  const day = 86400 * 1000;
  const cur: number[] = [];
  const prev: number[] = [];
  for (const m of items) {
    if (m.type !== type) continue;
    const v = pick(m);
    if (v === null) continue;
    const age = now - new Date(m.measured_at).getTime();
    if (age < 7 * day) cur.push(v);
    else if (age < 14 * day) prev.push(v);
  }
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  return { current: avg(cur), previous: avg(prev), currentCount: cur.length, previousCount: prev.length };
}
