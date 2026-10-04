"use client";

import { useMemo } from "react";
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
import type { Measurement } from "@/lib/types";
import { formatDateTime, formatShortDate } from "./utils";

// Kategorik renkler (sabit sıra): 1 mavi, 2 turuncu. Kimlik yalnızca renge bırakılmaz — lejant + farklı işaretçi.
const SERIES_1 = "#2a78d6";
const SERIES_2 = "#eb6834";
const GRID = "#e5e5e5";
const AXIS = { fontSize: 12, fill: "#737373" };

interface Point {
  t: number;
  value: number;
}

function tooltipLabel(label: unknown): string {
  return typeof label === "number" ? formatDateTime(new Date(label).toISOString()) : "";
}

function EmptyChart({ text }: { text: string }) {
  return <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">{text}</p>;
}

export function GlucoseChart({ data }: { data: Measurement[] }) {
  const { fasting, post } = useMemo(() => {
    const toPoint = (m: Measurement): Point => ({ t: new Date(m.measured_at).getTime(), value: m.value });
    return {
      fasting: data.filter((m) => m.type === "glucose_fasting").map(toPoint),
      post: data.filter((m) => m.type === "glucose_postprandial").map(toPoint),
    };
  }, [data]);

  if (fasting.length + post.length === 0) return <EmptyChart text="Son 30 günde kan şekeri ölçümü yok." />;

  return (
    <figure aria-label="Son 30 gün kan şekeri grafiği">
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatShortDate}
              tick={AXIS}
              stroke={GRID}
              allowDuplicatedCategory={false}
            />
            <YAxis tick={AXIS} stroke={GRID} domain={[40, "auto"]} unit="" />
            <ReferenceArea y1={70} y2={130} fill="#10b981" fillOpacity={0.08} ifOverflow="extendDomain" />
            <Tooltip
              labelFormatter={tooltipLabel}
              formatter={(v) => [`${v} mg/dL`]}
              contentStyle={{ borderRadius: 8, fontSize: 12 }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line
              data={fasting}
              dataKey="value"
              name="Açlık"
              stroke={SERIES_1}
              strokeWidth={2}
              dot={{ r: 4, fill: SERIES_1, stroke: "#fff", strokeWidth: 2 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
            />
            <Line
              data={post}
              dataKey="value"
              name="Tokluk"
              stroke={SERIES_2}
              strokeWidth={2}
              strokeDasharray="5 3"
              dot={{ r: 4, fill: SERIES_2, stroke: "#fff", strokeWidth: 2 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-muted-foreground">
        Yeşil bant: açlık kan şekeri için hedef aralık 70–130 mg/dL. Hedefiniz için hekiminize danışın.
      </figcaption>
    </figure>
  );
}

export function BloodPressureChart({ data }: { data: Measurement[] }) {
  const points = useMemo(
    () =>
      data
        .filter((m) => m.type === "blood_pressure")
        .map((m) => ({ t: new Date(m.measured_at).getTime(), sys: m.value, dia: m.value2 })),
    [data],
  );

  if (points.length === 0) return <EmptyChart text="Son 30 günde tansiyon ölçümü yok." />;

  return (
    <figure aria-label="Son 30 gün tansiyon grafiği">
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatShortDate}
              tick={AXIS}
              stroke={GRID}
            />
            <YAxis tick={AXIS} stroke={GRID} domain={[40, "auto"]} />
            <ReferenceLine y={130} stroke={SERIES_1} strokeDasharray="4 4" strokeOpacity={0.6} label={{ value: "130", position: "insideTopRight", fontSize: 11, fill: "#737373" }} />
            <ReferenceLine y={80} stroke={SERIES_2} strokeDasharray="4 4" strokeOpacity={0.6} label={{ value: "80", position: "insideTopRight", fontSize: 11, fill: "#737373" }} />
            <Tooltip
              labelFormatter={tooltipLabel}
              formatter={(v, name) => [`${v} mmHg`, name]}
              contentStyle={{ borderRadius: 8, fontSize: 12 }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line
              dataKey="sys"
              name="Büyük (sistolik)"
              stroke={SERIES_1}
              strokeWidth={2}
              dot={{ r: 4, fill: SERIES_1, stroke: "#fff", strokeWidth: 2 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
            />
            <Line
              dataKey="dia"
              name="Küçük (diyastolik)"
              stroke={SERIES_2}
              strokeWidth={2}
              strokeDasharray="5 3"
              dot={{ r: 4, fill: SERIES_2, stroke: "#fff", strokeWidth: 2 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-muted-foreground">
        Kesikli çizgiler: 130/80 mmHg referans değerleri. Hedefiniz için hekiminize danışın.
      </figcaption>
    </figure>
  );
}
