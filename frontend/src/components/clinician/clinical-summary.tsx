"use client";

import { ArrowDownRight, ArrowRight, ArrowUpRight, Stethoscope } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ClinicalInsights } from "@/lib/types";
import { cn } from "@/lib/utils";
import { pct } from "./format";

const TREND_TEXT = {
  improving: { Icon: ArrowUpRight, text: "arttı", className: "text-emerald-700" },
  stable: { Icon: ArrowRight, text: "değişmedi", className: "text-muted-foreground" },
  worsening: { Icon: ArrowDownRight, text: "düştü", className: "text-red-700" },
} as const;

function Stat({
  label,
  value,
  hint,
  tone = "default",
  children,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "warning";
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-1 flex items-center gap-1 text-2xl font-semibold tabular-nums", tone === "warning" && "text-amber-700")}>
        {value}
        {children}
      </p>
      {hint && <p className="mt-1 text-xs text-muted-foreground tabular-nums">{hint}</p>}
    </div>
  );
}

/** Hekim için deterministik klinik özet (GET /patients/{id}/insights). Teşhis veya tedavi önerisi değildir. */
export function ClinicalSummaryCard({ insights }: { insights: ClinicalInsights }) {
  const { adherence: a, glucose: g, blood_pressure: bp, measurement_frequency: f } = insights;
  const trend = a.trend ? TREND_TEXT[a.trend] : null;
  const reasons = insights.skip_reasons;
  const maxReason = Math.max(1, ...reasons.map((r) => r.count));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Stethoscope className="size-4" aria-hidden /> Klinik özet
        </CardTitle>
        <CardDescription>
          Son {insights.period_days} gün · kayıtlardan otomatik hesaplanır; teşhis veya tedavi önerisi değildir
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {insights.highlights.length > 0 && (
          <ul className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950" aria-label="Öne çıkanlar">
            {insights.highlights.map((h) => (
              <li key={h} className="flex gap-2">
                <span aria-hidden>•</span>
                <span>{h}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Uyum (7 gün)"
            value={pct(a.current_7d)}
            hint={a.previous_7d !== null ? `Önceki 7 gün: ${pct(a.previous_7d)}` : "Önceki hafta verisi yok"}
            tone={a.trend === "worsening" ? "warning" : "default"}
          >
            {trend && (
              <span className={cn("inline-flex", trend.className)} title={`Önceki haftaya göre ${trend.text}`}>
                <trend.Icon className="size-5" aria-hidden />
                <span className="sr-only">Önceki haftaya göre {trend.text}</span>
              </span>
            )}
          </Stat>
          <Stat
            label={`Kan şekeri hedefte (${g.target})`}
            value={g.count ? pct(g.time_in_range) : "—"}
            hint={g.count ? `${g.count} ölçüm · ${g.below} düşük · ${g.above} yüksek` : "Ölçüm yok"}
            tone={g.time_in_range !== null && g.time_in_range < 0.7 ? "warning" : "default"}
          />
          <Stat
            label={`Tansiyon hedefte (${bp.target})`}
            value={bp.count ? pct(bp.at_target) : "—"}
            hint={bp.count ? `${bp.count} ölçüm · ${bp.above} yüksek · ${bp.below} düşük` : "Ölçüm yok"}
            tone={bp.at_target !== null && bp.at_target < 0.5 ? "warning" : "default"}
          />
          <Stat
            label="Ölçüm sıklığı"
            value={`${f.per_week.toLocaleString("tr-TR")}/hafta`}
            hint={
              f.days_since_last === null
                ? "Hiç ölçüm yok"
                : `Ölçümlü gün: ${f.days_with_measurement}/${insights.period_days} · son: ${
                    f.days_since_last === 0 ? "bugün" : `${f.days_since_last} gün önce`
                  }`
            }
            tone={f.days_since_last === null || f.days_since_last >= 3 ? "warning" : "default"}
          />
        </div>

        <div>
          <h3 className="text-sm font-medium">Doz atlama nedenleri</h3>
          {reasons.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">Bu dönemde atlanan doz kaydı yok.</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {reasons.map((r) => (
                <li key={r.category} className="grid grid-cols-[minmax(0,12rem)_1fr_2rem] items-center gap-2 text-sm">
                  <span className="truncate" title={r.label}>
                    {r.label}
                  </span>
                  <span className="h-2 rounded-full bg-muted" aria-hidden>
                    <span
                      className="block h-2 rounded-full bg-teal-700"
                      style={{ width: `${(r.count / maxReason) * 100}%` }}
                    />
                  </span>
                  <span className="text-right tabular-nums text-muted-foreground">{r.count}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-xs text-muted-foreground">Hastanın yazdığı nedenler anahtar kelimeyle gruplanır.</p>
        </div>
      </CardContent>
    </Card>
  );
}
