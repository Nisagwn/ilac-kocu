"use client";

import { CalendarX2, ClipboardList } from "lucide-react";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { insightsApi } from "@/lib/api";
import type { ClinicalInsights, MeasurementType, TargetStats } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AdherenceTrendIndicator } from "./adherence-trend";
import { MEASUREMENT_LABEL } from "./format";
import { ErrorState, Skeleton } from "./states";
import { useAsync } from "./use-async";

// measurement-charts.tsx ile aynı palet (1. kategorik slot)
const BAR = "#2a78d6";
const GRID = "#e5e7eb";
const AXIS = "#6b7280";

const TYPES: MeasurementType[] = ["glucose_fasting", "glucose_postprandial", "blood_pressure"];

function ratioPct(s: TargetStats): number | null {
  return s.in_target_ratio === null ? null : Math.round(s.in_target_ratio * 100);
}

function avgText(type: MeasurementType, s: TargetStats): string {
  if (s.avg === null) return "";
  if (type === "blood_pressure") return `ort. ${Math.round(s.avg)}/${s.avg_diastolic !== null ? Math.round(s.avg_diastolic) : "—"} mmHg`;
  return `ort. ${Math.round(s.avg)} mg/dL`;
}

/** Hedefte kalma oranı için küçük yatay çubuk; görünür metin aynı bilgiyi taşır (çubuk dekoratif). */
function TargetBar({ label, type, stats }: { label: string; type: MeasurementType; stats: TargetStats }) {
  const p = ratioPct(stats);
  const tone = p === null ? "bg-muted" : p >= 70 ? "bg-emerald-600" : p >= 50 ? "bg-amber-500" : "bg-red-600";
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">
          {p === null ? (
            <span className="text-muted-foreground">Ölçüm yok</span>
          ) : (
            <>
              <span className="font-semibold">%{p}</span> hedefte ({stats.in_target}/{stats.count})
              {stats.avg !== null && <span className="text-muted-foreground"> · {avgText(type, stats)}</span>}
            </>
          )}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${p ?? 0}%` }} />
      </div>
    </div>
  );
}

function TargetsSection({ insights }: { insights: ClinicalInsights }) {
  const visible = TYPES.filter((t) => insights.targets[t].window_30d.count > 0);
  return (
    <section aria-labelledby="cs-targets" className="space-y-3">
      <h3 id="cs-targets" className="text-sm font-medium">
        Hedef aralıkta kalan ölçümler
      </h3>
      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">Son 30 günde ölçüm girilmemiş.</p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((t) => {
            const w = insights.targets[t];
            return (
              <li key={t} className="space-y-2 rounded-lg border p-3">
                <p className="text-sm font-medium">
                  {MEASUREMENT_LABEL[t]} <span className="text-xs font-normal text-muted-foreground">hedef {w.target}</span>
                </p>
                <TargetBar label="Son 30 gün" type={t} stats={w.window_30d} />
                <TargetBar label="Son 7 gün" type={t} stats={w.window_7d} />
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Genel erişkin hedefleri (ADA 2024): açlık 80–130, tokluk &lt;180 mg/dL, tansiyon &lt;130/80 mmHg. Bireysel
        hedef hekimce belirlenir.
      </p>
    </section>
  );
}

function SkipReasonsSection({ insights }: { insights: ClinicalInsights }) {
  const { total, categories, window_days } = insights.skip_reasons;
  const rows = categories.filter((c) => c.count > 0).sort((a, b) => b.count - a.count);
  const summary = rows.map((c) => `${c.label}: ${c.count} (%${c.percent})`).join("; ");
  return (
    <section aria-labelledby="cs-skip" className="space-y-2">
      <h3 id="cs-skip" className="text-sm font-medium">
        Doz atlama nedenleri{" "}
        <span className="font-normal text-muted-foreground">
          · son {window_days} gün, {total} atlanan doz
        </span>
      </h3>
      {total === 0 ? (
        <p className="text-sm text-muted-foreground">Bu dönemde &quot;almadım&quot; olarak işaretlenen doz yok.</p>
      ) : (
        <>
          <div role="img" aria-label={`Doz atlama nedenleri: ${summary}`} style={{ height: rows.length * 36 + 24 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 0 }}>
                <CartesianGrid horizontal={false} stroke={GRID} />
                <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: AXIS }} stroke={AXIS} />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={118}
                  tick={{ fontSize: 11, fill: AXIS }}
                  stroke={AXIS}
                  interval={0}
                />
                <Tooltip
                  cursor={{ fill: "rgba(0,0,0,0.04)" }}
                  formatter={(value, _name, item) => [`${value} doz (%${item.payload.percent})`, "Atlanan"]}
                />
                <Bar dataKey="count" fill={BAR} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false}>
                  <LabelList dataKey="count" position="right" style={{ fontSize: 11, fill: "#374151" }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <ul className="sr-only">
            {rows.map((c) => (
              <li key={c.key}>
                {c.label}: {c.count} doz, yüzde {c.percent}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Nedenler hastanın serbest metninden anahtar kelimelerle gruplanır; yaklaşık bir dağılımdır.
          </p>
        </>
      )}
    </section>
  );
}

export function ClinicalSummaryCard({ patientId, className }: { patientId: number; className?: string }) {
  const { data, error, loading, reload } = useAsync(() => insightsApi.insights(patientId), [patientId]);
  const trend = data?.adherence_trend;
  const freq = data?.measurement_frequency;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardList className="size-4" aria-hidden /> Klinik özet
        </CardTitle>
        <CardDescription>
          Hekim değerlendirmesi için veri özeti; teşhis veya tedavi önerisi değildir.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {error && !data && <ErrorState message={error} onRetry={reload} />}
        {loading && !data && (
          <div role="status" aria-label="Klinik özet yükleniyor" className="space-y-3">
            <Skeleton className="h-10" />
            <Skeleton className="h-24" />
          </div>
        )}
        {data && trend && freq && (
          <>
            <div className="flex flex-wrap gap-3">
              <div className="min-w-40 flex-1 rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">Uyum: bu hafta / geçen hafta</p>
                <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-lg font-semibold tabular-nums">
                  {trend.current_percent !== null ? `%${trend.current_percent}` : "—"}
                  <span className="text-sm font-normal text-muted-foreground">
                    / {trend.previous_percent !== null ? `%${trend.previous_percent}` : "—"}
                  </span>
                  <AdherenceTrendIndicator delta={trend.delta_points} direction={trend.direction} />
                </p>
              </div>
              <div
                className={cn(
                  "min-w-40 flex-1 rounded-lg border p-3",
                  freq.low_data && "border-amber-200 bg-amber-50 text-amber-900",
                )}
              >
                <p className={cn("text-xs", freq.low_data ? "text-amber-800" : "text-muted-foreground")}>
                  Ölçüm sıklığı · son {freq.window_days} gün
                </p>
                <p className="mt-1 text-lg font-semibold tabular-nums">
                  {freq.days_with_measurement}/{freq.window_days} gün
                </p>
                {freq.low_data && (
                  <p className="mt-1 flex items-start gap-1 text-xs" role="note">
                    <CalendarX2 className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                    Veri eksik: {freq.min_expected_days} günden az ölçüm girilmiş; oranlar temsil edici olmayabilir.
                  </p>
                )}
              </div>
            </div>
            <TargetsSection insights={data} />
            <SkipReasonsSection insights={data} />
          </>
        )}
      </CardContent>
    </Card>
  );
}
