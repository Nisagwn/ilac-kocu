"use client";

import Link from "next/link";
import { ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, Bell, CalendarDays, FileText, HeartPulse, Pill } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { Adherence, Alert, DoseLog, Measurement, Medication, MedicationCatalogFields, Patient, WeeklySummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AlertItem } from "./alert-item";
import { AdherenceBadge, ReadOnlyBadge } from "./badges";
import { DoseCalendar } from "./dose-calendar";
import {
  BAND_LABEL,
  MEASUREMENT_LABEL,
  bandOf,
  formatDateTime,
  isBpAboveTarget,
  isGlucoseOutOfRange,
  isOutOfRange,
  pct,
  periodTrend,
  relativeTime,
  type PeriodTrend,
} from "./format";
import { BloodPressureChart, GlucoseChart } from "./measurement-charts";
import { EmptyState, ErrorState, LoadingState, Skeleton } from "./states";
import { useAsync } from "./use-async";

interface DetailData {
  patient: Patient;
  adherence7: Adherence;
  adherence30: Adherence;
  medications: Medication[];
  measurements: Measurement[];
  doses: DoseLog[];
  summary: WeeklySummary;
}

async function loadDetail(id: number): Promise<DetailData> {
  const [patient, adherence7, adherence30, medications, measurements, doses, summary] = await Promise.all([
    api.patient(id),
    api.adherence(id, 7),
    api.adherence(id, 30),
    api.medications(id),
    api.measurements(id, 30),
    api.doses(id, 30),
    api.summary(id),
  ]);
  return { patient, adherence7, adherence30, medications, measurements, doses, summary };
}

export interface PatientDetailProps {
  patientId: number;
  /** Bakım veren görünümü: uyarı onaylama gibi tüm eylemler gizlenir. */
  readOnly: boolean;
}

export function PatientDetail({ patientId, readOnly }: PatientDetailProps) {
  const backHref = readOnly ? "/yakin" : "/hekim";
  const { data, error, loading, reload } = useAsync(() => loadDetail(patientId), [patientId]);

  return (
    <div className="space-y-6">
      <Link
        href={backHref}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden /> {readOnly ? "Hastalarım" : "Hasta listesi"}
      </Link>

      {error && !data && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && (
        <div className="space-y-4" role="status" aria-label="Hasta verileri yükleniyor">
          <Skeleton className="h-16" />
          <div className="grid gap-4 lg:grid-cols-3">
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
          </div>
          <Skeleton className="h-72" />
        </div>
      )}

      {data && (
        <>
          <PatientHeader patient={data.patient} readOnly={readOnly} />
          <ClinicalTrends measurements={data.measurements} />
          <div className="grid gap-4 lg:grid-cols-5">
            <AdherenceCard
              className="lg:col-span-3"
              a7={data.adherence7}
              a30={data.adherence30}
              medications={data.medications}
            />
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <CalendarDays className="size-4" aria-hidden /> 30 günlük doz takvimi
                </CardTitle>
                <CardDescription>Gün bazında planlı dozların durumu</CardDescription>
              </CardHeader>
              <CardContent>
                <DoseCalendar doses={data.doses} adherence={data.adherence30} />
              </CardContent>
            </Card>
          </div>
          <MeasurementsCard patient={data.patient} measurements={data.measurements} />
          <div className="grid gap-4 lg:grid-cols-2">
            <AlertsCard patientId={patientId} readOnly={readOnly} />
            <SummaryCard summary={data.summary} />
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function PatientHeader({ patient, readOnly }: { patient: Patient; readOnly: boolean }) {
  const age = patient.birth_year ? new Date().getFullYear() - patient.birth_year : null;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold">{patient.full_name}</h1>
        <p className="text-sm text-muted-foreground">
          {age !== null ? `${age} yaş` : "Yaş bilgisi yok"} · Hasta #{patient.id}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {patient.has_diabetes && (
          <Badge variant="outline" className="border-violet-200 bg-violet-50 text-violet-800">
            Tip 2 diyabet
          </Badge>
        )}
        {patient.has_hypertension && (
          <Badge variant="outline" className="border-rose-200 bg-rose-50 text-rose-800">
            Hipertansiyon
          </Badge>
        )}
        {!patient.has_diabetes && !patient.has_hypertension && <Badge variant="outline">Tanı bilgisi yok</Badge>}
        {readOnly && <ReadOnlyBadge />}
      </div>
    </div>
  );
}

// --- Klinik eğilim kutucukları ------------------------------------------------

interface TrendSpec {
  label: string;
  unit: string;
  trend: PeriodTrend;
  /** Bu değer hedef dışı mı? */
  isOut: (v: number) => boolean;
  /** Anlamlı değişim eşiği. */
  delta: number;
}

function TrendTile({ spec }: { spec: TrendSpec }) {
  const { current, previous, currentCount } = spec.trend;
  const diff = current !== null && previous !== null ? current - previous : null;
  const out = current !== null && spec.isOut(current);
  const rising = diff !== null && diff >= spec.delta;
  const falling = diff !== null && diff <= -spec.delta;
  const Arrow = rising ? ArrowUpRight : falling ? ArrowDownRight : ArrowRight;
  return (
    <Card size="sm" className={cn(out && "ring-red-300")}>
      <CardContent className="space-y-1">
        <p className="text-xs text-muted-foreground">{spec.label} · 7 gün ort.</p>
        <p className={cn("text-2xl font-semibold tabular-nums", out && "text-red-700")}>
          {current !== null ? Math.round(current) : "—"}
          <span className="ml-1 text-xs font-normal text-muted-foreground">{spec.unit}</span>
        </p>
        <p className="text-xs text-muted-foreground">
          {current === null ? (
            "Son 7 günde ölçüm yok"
          ) : diff === null ? (
            `${currentCount} ölçüm · önceki dönem verisi yok`
          ) : (
            <span className={cn("inline-flex items-center gap-1", rising && "text-amber-700", out && rising && "text-red-700")}>
              <Arrow className="size-3.5" aria-hidden />
              {diff > 0 ? "+" : ""}
              {Math.round(diff)} {spec.unit} önceki 7 güne göre
              <span className="sr-only">{rising ? "(yükseliş)" : falling ? "(düşüş)" : "(değişim yok)"}</span>
            </span>
          )}
        </p>
        {out && <p className="text-xs font-medium text-red-700">Hedef aralığın dışında</p>}
      </CardContent>
    </Card>
  );
}

function ClinicalTrends({ measurements }: { measurements: Measurement[] }) {
  const specs: TrendSpec[] = [
    {
      label: "Açlık kan şekeri",
      unit: "mg/dL",
      trend: periodTrend(measurements, "glucose_fasting"),
      isOut: isGlucoseOutOfRange,
      delta: 10,
    },
    {
      label: "Tokluk kan şekeri",
      unit: "mg/dL",
      trend: periodTrend(measurements, "glucose_postprandial"),
      isOut: isGlucoseOutOfRange,
      delta: 15,
    },
    {
      label: "Sistolik",
      unit: "mmHg",
      trend: periodTrend(measurements, "blood_pressure"),
      isOut: (v) => isBpAboveTarget(v, null),
      delta: 5,
    },
    {
      label: "Diyastolik",
      unit: "mmHg",
      trend: periodTrend(measurements, "blood_pressure", (m) => m.value2),
      isOut: (v) => v >= 80,
      delta: 5,
    },
  ];
  const visible = specs.filter((s) => s.trend.current !== null || s.trend.previous !== null);
  if (visible.length === 0) return null;
  return (
    <section aria-label="Ölçüm eğilimleri" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {visible.map((s) => (
        <TrendTile key={s.label} spec={s} />
      ))}
    </section>
  );
}

// --- Uyum ---------------------------------------------------------------------

function AdherenceCard({
  a7,
  a30,
  medications,
  className,
}: {
  a7: Adherence;
  a30: Adherence;
  medications: Medication[];
  className?: string;
}) {
  const medById = new Map(medications.map((m) => [String(m.id), m]));
  const medIds = Array.from(new Set([...Object.keys(a30.by_medication), ...medications.map((m) => String(m.id))]));

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Pill className="size-4" aria-hidden /> İlaç uyumu
        </CardTitle>
        <CardDescription>Alınan doz / planlanan doz</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: "Son 7 gün", a: a7 },
            { label: "Son 30 gün", a: a30 },
          ].map(({ label, a }) => (
            <div key={label} className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{pct(a.score)}</p>
              <AdherenceBadge score={a.score} band={a.band} className="mt-1" />
              <p className="mt-2 text-xs text-muted-foreground tabular-nums">
                {a.taken}/{a.planned} alındı · {a.skipped} atlandı · {a.missing} kaydedilmedi
              </p>
            </div>
          ))}
        </div>

        {medIds.length === 0 ? (
          <p className="text-sm text-muted-foreground">Kayıtlı ilaç yok.</p>
        ) : (
          <Table>
            <caption className="sr-only">İlaç bazında 7 ve 30 günlük uyum</caption>
            <TableHeader>
              <TableRow>
                <TableHead>İlaç</TableHead>
                <TableHead>Saatler</TableHead>
                <TableHead>7 gün</TableHead>
                <TableHead>30 gün</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {medIds.map((id) => {
                const med = medById.get(id);
                const s7 = a7.by_medication[id];
                const s30 = a30.by_medication[id];
                return (
                  <TableRow key={id}>
                    <TableCell className="whitespace-normal">
                      <span className="font-medium">{med?.name ?? `İlaç #${id}`}</span>
                      {med?.dose_text && <span className="ml-1 text-xs text-muted-foreground">{med.dose_text}</span>}
                      {!med && <span className="ml-1 text-xs text-muted-foreground">(pasif)</span>}
                      {med && (med as Medication & MedicationCatalogFields).verified === false && (
                        <Badge
                          variant="outline"
                          className="ml-1.5 border-amber-200 bg-amber-50 text-amber-800"
                          title="Hasta bu ilacı katalogdan seçmeden serbest metinle ekledi; etken madde doğrulanmadı."
                        >
                          Doğrulanmamış
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {med?.schedules.map((s) => s.time_of_day.slice(0, 5)).join(", ") || "—"}
                    </TableCell>
                    <TableCell>
                      {s7 ? (
                        <AdherenceBadge score={s7.score} band={bandOf(s7.score)} />
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {s30 ? (
                        <span className="inline-flex items-center gap-2">
                          <AdherenceBadge score={s30.score} band={bandOf(s30.score)} />
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {s30.taken}/{s30.planned}
                          </span>
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

// --- Ölçümler -------------------------------------------------------------------

function formatValue(m: Measurement): string {
  return m.type === "blood_pressure" ? `${m.value}/${m.value2 ?? "—"} ${m.unit}` : `${m.value} ${m.unit}`;
}

function MeasurementsCard({ patient, measurements }: { patient: Patient; measurements: Measurement[] }) {
  const hasGlucose = measurements.some((m) => m.type !== "blood_pressure");
  const hasBp = measurements.some((m) => m.type === "blood_pressure");
  const showGlucose = patient.has_diabetes || hasGlucose;
  const showBp = patient.has_hypertension || hasBp;
  const outCount = measurements.filter(isOutOfRange).length;
  const recent = [...measurements].reverse();
  const defaultTab = showGlucose ? "glucose" : showBp ? "bp" : "table";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HeartPulse className="size-4" aria-hidden /> Son 30 gün ölçümler
        </CardTitle>
        <CardDescription>
          {measurements.length} ölçüm
          {measurements.length > 0 && (
            <>
              {" · "}
              <span className={cn(outCount > 0 && "font-medium text-red-700")}>{outCount} hedef dışı</span>
            </>
          )}
          {measurements.length > 0 && <> · son ölçüm {relativeTime(measurements[measurements.length - 1].measured_at)}</>}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {measurements.length === 0 && !showGlucose && !showBp ? (
          <EmptyState title="Son 30 günde ölçüm yok" />
        ) : (
          <Tabs defaultValue={defaultTab}>
            <TabsList>
              {showGlucose && <TabsTrigger value="glucose">Kan şekeri</TabsTrigger>}
              {showBp && <TabsTrigger value="bp">Tansiyon</TabsTrigger>}
              <TabsTrigger value="table">Tablo</TabsTrigger>
            </TabsList>
            {showGlucose && (
              <TabsContent value="glucose" className="pt-2">
                <GlucoseChart items={measurements} />
                <p className="mt-1 text-xs text-muted-foreground">
                  Yeşil bant 70–180 mg/dL hedef aralığıdır; büyük kırmızı noktalar hedef dışı ölçümlerdir.
                </p>
              </TabsContent>
            )}
            {showBp && (
              <TabsContent value="bp" className="pt-2">
                <BloodPressureChart items={measurements} />
                <p className="mt-1 text-xs text-muted-foreground">
                  Kesikli çizgiler 130/80 mmHg hedefidir; büyük kırmızı noktalar hedefin üzerindeki değerlerdir.
                </p>
              </TabsContent>
            )}
            <TabsContent value="table" className="pt-2">
              {recent.length === 0 ? (
                <EmptyState title="Son 30 günde ölçüm yok" />
              ) : (
                <div className="max-h-80 overflow-y-auto">
                  <Table>
                    <caption className="sr-only">Son 30 gün ölçümleri, en yeni önce</caption>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Tarih</TableHead>
                        <TableHead>Tür</TableHead>
                        <TableHead>Değer</TableHead>
                        <TableHead>Durum</TableHead>
                        <TableHead>Not</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recent.map((m) => {
                        const out = isOutOfRange(m);
                        return (
                          <TableRow key={m.id} className={cn(out && "bg-red-50/60")}>
                            <TableCell className="tabular-nums">{formatDateTime(m.measured_at)}</TableCell>
                            <TableCell>{MEASUREMENT_LABEL[m.type]}</TableCell>
                            <TableCell className={cn("font-medium tabular-nums", out && "text-red-700")}>
                              {formatValue(m)}
                            </TableCell>
                            <TableCell>
                              {out ? (
                                <span className="text-xs font-medium text-red-700">Hedef dışı</span>
                              ) : (
                                <span className="text-xs text-muted-foreground">Hedefte</span>
                              )}
                            </TableCell>
                            <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
                              {m.note ?? ""}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </TabsContent>
          </Tabs>
        )}
      </CardContent>
    </Card>
  );
}

// --- Uyarılar ------------------------------------------------------------------

function AlertsCard({ patientId, readOnly }: { patientId: number; readOnly: boolean }) {
  const { data, error, loading, reload, setData } = useAsync(() => api.patientAlerts(patientId), [patientId]);
  const open = data?.filter((a) => a.acknowledged_at === null).length ?? 0;

  function handleAcked(updated: Alert) {
    setData((prev) => (prev ? prev.map((a) => (a.id === updated.id ? updated : a)) : prev));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bell className="size-4" aria-hidden /> Uyarılar
        </CardTitle>
        <CardDescription>{data ? `${open} açık · toplam ${data.length}` : "—"}</CardDescription>
      </CardHeader>
      <CardContent>
        {error && <ErrorState message={error} onRetry={reload} />}
        {loading && !data && <LoadingState label="Uyarılar yükleniyor…" />}
        {data && data.length === 0 && <EmptyState title="Uyarı yok" />}
        {data && data.length > 0 && (
          <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {data.map((a) => (
              <AlertItem key={a.id} alert={a} onAcked={readOnly ? undefined : handleAcked} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// --- Haftalık özet ---------------------------------------------------------------

function SummaryCard({ summary }: { summary: WeeklySummary }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="size-4" aria-hidden /> Haftalık özet
        </CardTitle>
        <CardDescription>Son {summary.period_days} gün</CardDescription>
        <CardAction>
          <AdherenceBadge score={summary.adherence.score} band={summary.adherence.band} />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {summary.narrative && (
          <div className="rounded-lg border border-teal-200 bg-teal-50 p-3 text-sm text-teal-950">
            <p className="whitespace-pre-line">{summary.narrative}</p>
            <p className="mt-2 text-xs text-teal-800">
              Otomatik oluşturulmuş özettir; teşhis veya tedavi önerisi değildir.
            </p>
          </div>
        )}
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Uyum</dt>
            <dd className="font-medium tabular-nums">
              {pct(summary.adherence.score)} ({summary.adherence.taken}/{summary.adherence.planned} doz) ·{" "}
              {BAND_LABEL[summary.adherence.band]}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Uyarı (7 gün)</dt>
            <dd className={cn("font-medium tabular-nums", summary.alerts_last_7d > 0 && "text-amber-700")}>
              {summary.alerts_last_7d}
            </dd>
          </div>
        </dl>
        {summary.measurements.length === 0 ? (
          <p className="text-sm text-muted-foreground">Bu hafta ölçüm girilmemiş.</p>
        ) : (
          <Table>
            <caption className="sr-only">Haftalık ölçüm istatistikleri</caption>
            <TableHeader>
              <TableRow>
                <TableHead>Ölçüm</TableHead>
                <TableHead>Adet</TableHead>
                <TableHead>Ort.</TableHead>
                <TableHead>Min–Maks</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.measurements.map((m) => {
                const bp = m.type === "blood_pressure";
                const avgOut = bp ? isBpAboveTarget(m.avg, m.avg_diastolic) : isGlucoseOutOfRange(m.avg);
                return (
                  <TableRow key={m.type}>
                    <TableCell>{MEASUREMENT_LABEL[m.type]}</TableCell>
                    <TableCell className="tabular-nums">{m.count}</TableCell>
                    <TableCell className={cn("font-medium tabular-nums", avgOut && "text-red-700")}>
                      {bp ? `${m.avg}/${m.avg_diastolic ?? "—"}` : m.avg}
                    </TableCell>
                    <TableCell className="tabular-nums text-muted-foreground">
                      {m.min}–{m.max}
                      {bp && <span className="sr-only"> (sistolik)</span>}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
