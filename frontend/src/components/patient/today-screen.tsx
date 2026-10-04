"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { Activity, Check, ChevronRight, Clock, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PushToggle } from "@/components/push-toggle";
import { api } from "@/lib/api";
import type { Adherence, DoseStatus, TodayDose } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ErrorState, LoadingState, PageHeader } from "./states";
import { type AsyncState, useAsync } from "./use-async";
import { BAND_CLASS, BAND_LABEL, errorMessage, hhmm, localDateStr } from "./utils";

export function TodayScreen({ patientId }: { patientId: number }) {
  const today = useMemo(() => localDateStr(), []);
  const doses = useAsync(useCallback(() => api.todayDoses(patientId, today), [patientId, today]));
  const adherence = useAsync(useCallback(() => api.adherence(patientId, 7), [patientId]));
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [reasonFor, setReasonFor] = useState<number | null>(null);

  const groups = useMemo(() => {
    const map = new Map<string, TodayDose[]>();
    for (const d of doses.data ?? []) {
      const key = hhmm(d.time_of_day);
      map.set(key, [...(map.get(key) ?? []), d]);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [doses.data]);

  const { setData: setDoses } = doses;
  const { reload: reloadAdherence } = adherence;

  const log = useCallback(
    async (dose: TodayDose, status: DoseStatus, reason?: string) => {
      const previous = dose.status;
      setActionError(null);
      setPending((p) => new Set(p).add(dose.schedule_id));
      // İyimser güncelleme: kullanıcı anında geri bildirim görür, hata olursa geri alınır.
      setDoses((list) => list?.map((d) => (d.schedule_id === dose.schedule_id ? { ...d, status } : d)) ?? list);
      try {
        await api.logDose(patientId, {
          schedule_id: dose.schedule_id,
          scheduled_date: today,
          status,
          ...(reason ? { reason } : {}),
        });
        reloadAdherence();
      } catch (e) {
        setDoses((list) => list?.map((d) => (d.schedule_id === dose.schedule_id ? { ...d, status: previous } : d)) ?? list);
        setActionError(`${dose.medication_name}: ${errorMessage(e)}`);
        setReasonFor(null);
      } finally {
        setPending((p) => {
          const next = new Set(p);
          next.delete(dose.schedule_id);
          return next;
        });
      }
    },
    [patientId, today, setDoses, reloadAdherence],
  );

  const total = doses.data?.length ?? 0;
  const done = doses.data?.filter((d) => d.status !== null).length ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Bugün"
        description={new Date().toLocaleDateString("tr-TR", { weekday: "long", day: "numeric", month: "long" })}
      />

      <AdherenceCard state={adherence} />

      <PushToggle />

      <section aria-labelledby="doses-title" className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 id="doses-title" className="font-medium">
            Bugünkü dozlar
          </h2>
          {total > 0 && (
            <span className="text-sm text-muted-foreground">
              {done}/{total} işaretlendi
            </span>
          )}
        </div>

        {actionError && <ErrorState message={actionError} />}
        {doses.loading && !doses.data && <LoadingState label="Dozlar yükleniyor…" />}
        {doses.error && <ErrorState message={doses.error} onRetry={doses.reload} />}
        {doses.data && total === 0 && (
          <Card>
            <CardContent className="text-sm text-muted-foreground">
              Bugün için planlı doz yok.{" "}
              <Link href="/ilaclar" className="text-teal-700 underline">
                İlaç planını düzenle
              </Link>
            </CardContent>
          </Card>
        )}

        {groups.map(([time, items]) => (
          <div key={time} className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
              <Clock className="size-4" aria-hidden /> {time}
            </h3>
            {items.map((dose) => (
              <DoseCard
                key={dose.schedule_id}
                dose={dose}
                busy={pending.has(dose.schedule_id)}
                askReason={reasonFor === dose.schedule_id}
                onTaken={() => {
                  setReasonFor(null);
                  void log(dose, "taken");
                }}
                onSkipped={() => {
                  setReasonFor(dose.schedule_id);
                  void log(dose, "skipped");
                }}
                onReason={(reason) => {
                  setReasonFor(null);
                  if (reason) void log(dose, "skipped", reason);
                }}
              />
            ))}
          </div>
        ))}
      </section>

      <Link
        href="/olcum"
        className={cn(buttonVariants({ variant: "outline" }), "h-12 w-full justify-between px-4 text-base")}
      >
        <span className="flex items-center gap-2">
          <Activity className="size-5 text-teal-700" aria-hidden /> Ölçüm gir
        </span>
        <ChevronRight className="size-5" aria-hidden />
      </Link>
    </div>
  );
}

function AdherenceCard({ state }: { state: AsyncState<Adherence> }) {
  const a = state.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Son 7 gün ilaç uyumu</CardTitle>
        <CardDescription>Planlı dozlardan alınanların oranı</CardDescription>
      </CardHeader>
      <CardContent>
        {state.error && !a && <ErrorState message={state.error} onRetry={state.reload} />}
        {!a && !state.error && <LoadingState />}
        {a && (
          <div className="space-y-3">
            <div className="flex items-end gap-3">
              <span className="text-4xl font-semibold tabular-nums">{a.percent === null ? "—" : `%${a.percent}`}</span>
              <span className={cn("mb-1 rounded-full px-2 py-0.5 text-xs font-medium", BAND_CLASS[a.band])}>
                {BAND_LABEL[a.band]}
              </span>
            </div>
            {a.percent !== null && (
              <div
                className="h-2 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label="7 günlük uyum"
                aria-valuenow={a.percent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div className="h-full rounded-full bg-teal-600" style={{ width: `${a.percent}%` }} />
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              {a.planned} planlı · {a.taken} alındı · {a.skipped} alınmadı · {a.missing} işaretlenmedi
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DoseCard({
  dose,
  busy,
  askReason,
  onTaken,
  onSkipped,
  onReason,
}: {
  dose: TodayDose;
  busy: boolean;
  askReason: boolean;
  onTaken: () => void;
  onSkipped: () => void;
  onReason: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const reasonId = `reason-${dose.schedule_id}`;
  return (
    <Card
      size="sm"
      className={cn(
        dose.status === "taken" && "ring-emerald-300 bg-emerald-50/50",
        dose.status === "skipped" && "ring-amber-300 bg-amber-50/50",
      )}
    >
      <CardContent className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-base font-medium">{dose.medication_name}</p>
            {dose.dose_text && <p className="text-sm text-muted-foreground">{dose.dose_text}</p>}
          </div>
          {dose.status === "taken" && <span className="text-sm font-medium text-emerald-700">Alındı</span>}
          {dose.status === "skipped" && <span className="text-sm font-medium text-amber-700">Alınmadı</span>}
        </div>
        <div className="grid grid-cols-2 gap-2" role="group" aria-label={`${dose.medication_name} ${hhmm(dose.time_of_day)} dozu`}>
          <Button
            className={cn(
              "h-12 text-base",
              dose.status === "taken" ? "bg-emerald-600 text-white hover:bg-emerald-600/90" : "",
            )}
            variant={dose.status === "taken" ? "default" : "outline"}
            aria-pressed={dose.status === "taken"}
            disabled={busy}
            onClick={onTaken}
          >
            <Check className="size-5" aria-hidden /> Aldım
          </Button>
          <Button
            className={cn("h-12 text-base", dose.status === "skipped" ? "bg-amber-500 text-white hover:bg-amber-500/90" : "")}
            variant={dose.status === "skipped" ? "default" : "outline"}
            aria-pressed={dose.status === "skipped"}
            disabled={busy}
            onClick={onSkipped}
          >
            <X className="size-5" aria-hidden /> Almadım
          </Button>
        </div>
        {askReason && (
          <form
            className="space-y-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              onReason(reason.trim());
            }}
          >
            <label htmlFor={reasonId} className="text-sm text-muted-foreground">
              Neden alamadınız? (isteğe bağlı)
            </label>
            <div className="flex gap-2">
              <Input
                id={reasonId}
                value={reason}
                maxLength={255}
                placeholder="ör. unuttum, ilacım bitti"
                className="h-10"
                onChange={(e) => setReason(e.target.value)}
              />
              <Button type="submit" className="h-10" disabled={busy}>
                {reason.trim() ? "Kaydet" : "Geç"}
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
