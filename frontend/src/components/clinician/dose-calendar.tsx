"use client";

import type { Adherence, DoseLog } from "@/lib/types";
import { cn } from "@/lib/utils";
import { dateKey, parseDateKey } from "./format";

type DayStatus = "taken" | "partial" | "skipped" | "missing" | "none";

const STATUS: Record<DayStatus, { label: string; glyph: string; className: string }> = {
  taken: { label: "Tümü alındı", glyph: "✓", className: "bg-emerald-500 text-white" },
  partial: { label: "Kısmen alındı", glyph: "½", className: "bg-amber-300 text-amber-950" },
  skipped: { label: "Atlandı", glyph: "–", className: "bg-orange-500 text-white" },
  missing: { label: "Kaydedilmedi", glyph: "✕", className: "bg-red-600 text-white" },
  none: { label: "Planlı doz yok / henüz zamanı gelmedi", glyph: "", className: "bg-muted text-muted-foreground" },
};

const WEEKDAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];

interface DayCell {
  key: string;
  date: Date;
  taken: number;
  skipped: number;
  status: DayStatus;
}

function buildDays(doses: DoseLog[], adherence: Adherence): DayCell[] {
  const counts = new Map<string, { taken: number; skipped: number }>();
  for (const d of doses) {
    const c = counts.get(d.scheduled_date) ?? { taken: 0, skipped: 0 };
    if (d.status === "taken") c.taken += 1;
    else c.skipped += 1;
    counts.set(d.scheduled_date, c);
  }
  const missed = new Set(adherence.missed_dates);
  const days: DayCell[] = [];
  const end = parseDateKey(adherence.end);
  for (let d = parseDateKey(adherence.start); d <= end; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)) {
    const key = dateKey(d);
    const c = counts.get(key) ?? { taken: 0, skipped: 0 };
    let status: DayStatus;
    if (missed.has(key)) status = c.taken > 0 ? "partial" : c.skipped > 0 ? "skipped" : "missing";
    else status = c.taken > 0 ? "taken" : "none";
    days.push({ key, date: d, ...c, status });
  }
  return days;
}

/** 30 günlük doz takvimi: her hücre bir gün; renk + simge birlikte durumu anlatır. */
export function DoseCalendar({ doses, adherence }: { doses: DoseLog[]; adherence: Adherence }) {
  const days = buildDays(doses, adherence);
  const leading = days.length ? (days[0].date.getDay() + 6) % 7 : 0; // Pazartesi = 0
  const counts = days.reduce<Record<DayStatus, number>>(
    (acc, d) => ({ ...acc, [d.status]: acc[d.status] + 1 }),
    { taken: 0, partial: 0, skipped: 0, missing: 0, none: 0 },
  );

  return (
    <div className="space-y-3">
      <div className="max-w-md">
        <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-muted-foreground" aria-hidden>
          {WEEKDAYS.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>
        <ol className="mt-1 grid grid-cols-7 gap-1" aria-label="Son 30 gün doz takvimi">
          {Array.from({ length: leading }, (_, i) => (
            <li key={`pad-${i}`} aria-hidden />
          ))}
          {days.map((d) => {
            const s = STATUS[d.status];
            const dateLabel = d.date.toLocaleDateString("tr-TR", { day: "numeric", month: "long", weekday: "short" });
            const detail =
              d.taken || d.skipped ? ` (${d.taken} alındı${d.skipped ? `, ${d.skipped} atlandı` : ""})` : "";
            const label = `${dateLabel}: ${s.label}${detail}`;
            return (
              <li
                key={d.key}
                title={label}
                className={cn(
                  "relative flex aspect-square flex-col items-center justify-center rounded-md text-[11px] leading-none",
                  s.className,
                )}
              >
                <span className="sr-only">{label}</span>
                <span aria-hidden className="absolute top-0.5 left-1 text-[9px] opacity-80">
                  {d.date.getDate()}
                </span>
                <span aria-hidden className="text-sm font-semibold">
                  {s.glyph}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Takvim açıklaması">
        {(Object.keys(STATUS) as DayStatus[]).map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className={cn("flex size-4 items-center justify-center rounded text-[10px]", STATUS[k].className)}
            >
              {STATUS[k].glyph}
            </span>
            {k === "none" ? "Plan yok" : STATUS[k].label} ({counts[k]})
          </li>
        ))}
      </ul>
    </div>
  );
}
