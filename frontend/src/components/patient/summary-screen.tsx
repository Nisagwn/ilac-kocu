"use client";

import { useCallback } from "react";
import Link from "next/link";
import { AlertTriangle, Sparkles } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { WeeklySummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ErrorState, LoadingState, PageHeader } from "./states";
import { useAsync } from "./use-async";
import { BAND_CLASS, BAND_LABEL, MEASUREMENT_LABEL, MEASUREMENT_UNIT } from "./utils";

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

export function SummaryScreen({ patientId }: { patientId: number }) {
  const s = useAsync(useCallback(() => api.summary(patientId), [patientId]));

  return (
    <div className="space-y-4">
      <PageHeader title="Haftalık özet" description="Son 7 gün" />
      {s.loading && !s.data && <LoadingState label="Özet hazırlanıyor…" />}
      {s.error && <ErrorState message={s.error} onRetry={s.reload} />}
      {s.data && <SummaryBody data={s.data} />}
    </div>
  );
}

function SummaryBody({ data }: { data: WeeklySummary }) {
  const { adherence } = data;
  const percent = adherence.score === null ? null : Math.round(adherence.score * 100);

  return (
    <>
      {data.narrative && (
        <Card className="bg-teal-50/60 ring-teal-200">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="size-4 text-teal-700" aria-hidden /> Koçun değerlendirmesi
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap leading-relaxed">{data.narrative}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>İlaç uyumu</CardTitle>
            <CardDescription>Son {data.period_days} gün</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-end gap-3">
              <span className="text-4xl font-semibold tabular-nums">{percent === null ? "—" : `%${percent}`}</span>
              <span className={cn("mb-1 rounded-full px-2 py-0.5 text-xs font-medium", BAND_CLASS[adherence.band])}>
                {BAND_LABEL[adherence.band]}
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              {adherence.planned > 0
                ? `${adherence.planned} planlı dozun ${adherence.taken} tanesi alındı.`
                : "Bu hafta planlı doz yok."}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Uyarılar</CardTitle>
            <CardDescription>Hedef dışı ölçüm ve koç bildirimleri</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-2">
              {data.alerts_last_7d > 0 && <AlertTriangle className="size-6 text-amber-600" aria-hidden />}
              <span className="text-4xl font-semibold tabular-nums">{data.alerts_last_7d}</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {data.alerts_last_7d > 0
                ? "Bu uyarılar hekiminizin panelinde görünür."
                : "Bu hafta uyarı oluşmadı."}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ölçümler</CardTitle>
          <CardDescription>Türe göre son 7 gün istatistikleri</CardDescription>
        </CardHeader>
        <CardContent>
          {data.measurements.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Bu hafta ölçüm yok.{" "}
              <Link href="/olcum" className="text-teal-700 underline">
                Ölçüm gir
              </Link>
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Ölçüm istatistikleri</caption>
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th scope="col" className="py-2 pr-2 font-medium">Tür</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">Sayı</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">Ortalama</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">En düşük</th>
                    <th scope="col" className="py-2 pl-2 text-right font-medium">En yüksek</th>
                  </tr>
                </thead>
                <tbody>
                  {data.measurements.map((m) => {
                    const isBp = m.type === "blood_pressure";
                    return (
                      <tr key={m.type} className="border-b last:border-0">
                        <th scope="row" className="py-2 pr-2 text-left font-medium">
                          {MEASUREMENT_LABEL[m.type]}
                          <span className="block text-xs font-normal text-muted-foreground">
                            {MEASUREMENT_UNIT[m.type]}
                            {isBp ? " · büyük tansiyon" : ""}
                          </span>
                        </th>
                        <td className="px-2 py-2 text-right tabular-nums">{m.count}</td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          {isBp && m.avg_diastolic !== null ? `${fmt(m.avg)}/${fmt(m.avg_diastolic)}` : fmt(m.avg)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">{fmt(m.min)}</td>
                        <td className="py-2 pl-2 text-right tabular-nums">{fmt(m.max)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {data.measurements.some((m) => m.type === "blood_pressure") && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Tansiyon için en düşük/en yüksek büyük (sistolik) değerdir; ortalama büyük/küçük olarak verilir.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
