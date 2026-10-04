"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DotItemDotProps } from "recharts";
import type { Measurement } from "@/lib/types";
import {
  DIASTOLIC_TARGET,
  GLUCOSE_HIGH,
  GLUCOSE_LOW,
  SYSTOLIC_TARGET,
  formatDateTime,
  formatShortDate,
  isGlucoseOutOfRange,
} from "./format";
import { EmptyState } from "./states";

// Kategorik palet (dataviz referans paleti, 1. ve 2. slot). Hedef dışı noktalar ayrıca
// kırmızı halka + büyük işaretle vurgulanır; tablo görünümü renk-dışı erişim sağlar.
const SERIES_1 = "#2a78d6";
const SERIES_2 = "#eb6834";
const OUT_OF_RANGE = "#dc2626";
const GRID = "#e5e7eb";
const AXIS = "#6b7280";

interface GlucosePoint {
  t: number;
  fasting?: number;
  postprandial?: number;
}

interface BpPoint {
  t: number;
  systolic: number;
  diastolic: number | null;
}

type Checker = (value: number) => boolean;

/** Hedef dışı değerleri büyük, kırmızı halkalı noktayla gösteren dot fabrikası. */
function makeDot(color: string, isOut: Checker) {
  function RangeDot(props: DotItemDotProps) {
    const { cx, cy, value, index } = props;
    if (typeof cx !== "number" || typeof cy !== "number" || typeof value !== "number") return null;
    const out = isOut(value);
    return (
      <circle
        key={index}
        cx={cx}
        cy={cy}
        r={out ? 5 : 3}
        fill={out ? OUT_OF_RANGE : color}
        stroke="#ffffff"
        strokeWidth={2}
      />
    );
  }
  return RangeDot;
}

const glucoseDotFasting = makeDot(SERIES_1, isGlucoseOutOfRange);
const glucoseDotPost = makeDot(SERIES_2, isGlucoseOutOfRange);
const systolicDot = makeDot(SERIES_1, (v) => v >= SYSTOLIC_TARGET || v < 90);
const diastolicDot = makeDot(SERIES_2, (v) => v >= DIASTOLIC_TARGET);

function timeDomain(points: { t: number }[]): [number, number] {
  const now = Date.now();
  const min = Math.min(now - 30 * 86400 * 1000, ...points.map((p) => p.t));
  return [min, now];
}

function dayTicks([min, max]: [number, number]): number[] {
  const step = 5 * 86400 * 1000;
  const ticks: number[] = [];
  for (let t = max; t >= min; t -= step) ticks.unshift(t);
  return ticks;
}

const tooltipLabel = (label: unknown) => (typeof label === "number" ? formatDateTime(new Date(label).toISOString()) : "");

export function GlucoseChart({ items }: { items: Measurement[] }) {
  const points: GlucosePoint[] = items
    .filter((m) => m.type === "glucose_fasting" || m.type === "glucose_postprandial")
    .map((m) => ({
      t: new Date(m.measured_at).getTime(),
      ...(m.type === "glucose_fasting" ? { fasting: m.value } : { postprandial: m.value }),
    }));

  if (points.length === 0) return <EmptyState title="Son 30 günde kan şekeri ölçümü yok" />;
  const domain = timeDomain(points);
  const maxVal = Math.max(GLUCOSE_HIGH + 40, ...points.map((p) => p.fasting ?? p.postprandial ?? 0));

  return (
    <figure aria-label="Son 30 gün kan şekeri grafiği" className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <ReferenceArea
            y1={GLUCOSE_LOW}
            y2={GLUCOSE_HIGH}
            fill="#10b981"
            fillOpacity={0.08}
            ifOverflow="extendDomain"
            label={{ value: `Hedef ${GLUCOSE_LOW}–${GLUCOSE_HIGH}`, position: "insideTopRight", fill: AXIS, fontSize: 11 }}
          />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={domain}
            ticks={dayTicks(domain)}
            tickFormatter={formatShortDate}
            stroke={AXIS}
            fontSize={11}
            tickLine={false}
          />
          <YAxis domain={[40, Math.ceil(maxVal / 20) * 20]} stroke={AXIS} fontSize={11} tickLine={false} unit="" />
          <Tooltip
            labelFormatter={tooltipLabel}
            formatter={(v) => `${v} mg/dL`}
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            name="Açlık"
            dataKey="fasting"
            stroke={SERIES_1}
            strokeWidth={2}
            dot={glucoseDotFasting}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            name="Tokluk"
            dataKey="postprandial"
            stroke={SERIES_2}
            strokeWidth={2}
            dot={glucoseDotPost}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

export function BloodPressureChart({ items }: { items: Measurement[] }) {
  const points: BpPoint[] = items
    .filter((m) => m.type === "blood_pressure")
    .map((m) => ({ t: new Date(m.measured_at).getTime(), systolic: m.value, diastolic: m.value2 }));

  if (points.length === 0) return <EmptyState title="Son 30 günde tansiyon ölçümü yok" />;
  const domain = timeDomain(points);
  const maxVal = Math.max(160, ...points.map((p) => p.systolic));
  const minVal = Math.min(60, ...points.map((p) => p.diastolic ?? 60));

  return (
    <figure aria-label="Son 30 gün tansiyon grafiği" className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <ReferenceLine
            y={SYSTOLIC_TARGET}
            stroke={SERIES_1}
            strokeDasharray="4 4"
            label={{ value: `Sistolik hedef <${SYSTOLIC_TARGET}`, position: "insideTopRight", fill: AXIS, fontSize: 11 }}
          />
          <ReferenceLine
            y={DIASTOLIC_TARGET}
            stroke={SERIES_2}
            strokeDasharray="4 4"
            label={{ value: `Diyastolik hedef <${DIASTOLIC_TARGET}`, position: "insideBottomRight", fill: AXIS, fontSize: 11 }}
          />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={domain}
            ticks={dayTicks(domain)}
            tickFormatter={formatShortDate}
            stroke={AXIS}
            fontSize={11}
            tickLine={false}
          />
          <YAxis
            domain={[Math.floor((minVal - 10) / 10) * 10, Math.ceil((maxVal + 10) / 10) * 10]}
            stroke={AXIS}
            fontSize={11}
            tickLine={false}
          />
          <Tooltip
            labelFormatter={tooltipLabel}
            formatter={(v) => `${v} mmHg`}
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            name="Sistolik"
            dataKey="systolic"
            stroke={SERIES_1}
            strokeWidth={2}
            dot={systolicDot}
            isAnimationActive={false}
          />
          <Line
            name="Diyastolik"
            dataKey="diastolic"
            stroke={SERIES_2}
            strokeWidth={2}
            dot={diastolicDot}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}
