"use client";

import { useMemo } from "react";
import { RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Alert, PatientSummary, Severity } from "@/lib/types";
import { AlertItem } from "./alert-item";
import { SEVERITY_ICON } from "./badges";
import { SEVERITY_LABEL } from "./format";
import { EmptyState, ErrorState, LoadingState } from "./states";
import { useAsync } from "./use-async";

const ORDER: Severity[] = ["critical", "warning", "info"];

const HEADING_CLASS: Record<Severity, string> = {
  critical: "text-red-700",
  warning: "text-amber-700",
  info: "text-sky-700",
};

export function AlertsBoard() {
  const { data, error, loading, reload, setData } = useAsync(
    () => Promise.all([api.openAlerts(), api.patients()]),
    [],
  );

  const names = useMemo(() => {
    const m = new Map<number, string>();
    (data?.[1] ?? []).forEach((p: PatientSummary) => m.set(p.id, p.full_name));
    return m;
  }, [data]);

  // Okundu işaretlenen uyarı listeden düşer (bu sayfa yalnızca açık uyarıları gösterir).
  function handleAcked(updated: Alert) {
    setData((prev) => (prev ? [prev[0].filter((a) => a.id !== updated.id), prev[1]] : prev));
  }

  const alerts = data?.[0] ?? [];
  const groups = ORDER.map((sev) => ({ sev, items: alerts.filter((a) => a.severity === sev) }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Açık uyarılar</h1>
          <p className="text-sm text-muted-foreground">
            Tüm hastalarınızdaki okunmamış uyarılar, önem derecesine göre gruplanmıştır.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
          <RotateCw aria-hidden className={loading ? "animate-spin" : undefined} /> Yenile
        </Button>
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Uyarılar yükleniyor…" />}

      {data && alerts.length === 0 && (
        <Card>
          <EmptyState title="Açık uyarı yok" description="Tüm uyarılar okundu olarak işaretlenmiş." />
        </Card>
      )}

      {data &&
        groups
          .filter((g) => g.items.length > 0)
          .map(({ sev, items }) => {
            const Icon = SEVERITY_ICON[sev];
            return (
              <Card key={sev} role="region" aria-labelledby={`grp-${sev}`}>
                <CardHeader>
                  <CardTitle id={`grp-${sev}`} className={`flex items-center gap-2 ${HEADING_CLASS[sev]}`}>
                    <Icon className="size-4" aria-hidden />
                    {SEVERITY_LABEL[sev]} ({items.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-2">
                    {items.map((a) => (
                      <AlertItem
                        key={a.id}
                        alert={a}
                        patientName={names.get(a.patient_id) ?? `Hasta #${a.patient_id}`}
                        patientHref={`/hekim/hasta/${a.patient_id}`}
                        onAcked={handleAcked}
                      />
                    ))}
                  </ul>
                </CardContent>
              </Card>
            );
          })}
    </div>
  );
}
