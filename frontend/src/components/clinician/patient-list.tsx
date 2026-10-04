"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, ChevronRight, Flag, TrendingDown, Users, type LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { PatientSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AdherenceBadge } from "./badges";
import { bandOf, pct, relativeTime } from "./format";
import { EmptyState, ErrorState, Skeleton } from "./states";
import { useAsync } from "./use-async";

function KpiTile({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  icon: LucideIcon;
  tone?: "default" | "critical" | "warning";
}) {
  return (
    <Card size="sm">
      <CardContent className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p
            className={cn(
              "mt-1 text-2xl font-semibold tabular-nums",
              tone === "critical" && "text-red-700",
              tone === "warning" && "text-amber-700",
            )}
          >
            {value}
          </p>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <Icon
          aria-hidden
          className={cn(
            "size-5 text-muted-foreground",
            tone === "critical" && "text-red-600",
            tone === "warning" && "text-amber-600",
          )}
        />
      </CardContent>
    </Card>
  );
}

function Kpis({ rows }: { rows: PatientSummary[] }) {
  const critical = rows.reduce((n, r) => n + r.critical_alerts, 0);
  const criticalPatients = rows.filter((r) => r.critical_alerts > 0).length;
  const low = rows.filter((r) => r.band === "dusuk").length;
  const scored = rows.filter((r) => r.adherence_7d !== null);
  const avg = scored.length ? scored.reduce((n, r) => n + (r.adherence_7d ?? 0), 0) / scored.length : null;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiTile label="Hasta sayısı" value={String(rows.length)} icon={Users} />
      <KpiTile
        label="Kritik uyarı"
        value={String(critical)}
        hint={criticalPatients ? `${criticalPatients} hastada` : "Açık kritik uyarı yok"}
        icon={Flag}
        tone={critical ? "critical" : "default"}
      />
      <KpiTile
        label="Düşük uyum (<%60)"
        value={String(low)}
        hint="Son 7 gün"
        icon={TrendingDown}
        tone={low ? "warning" : "default"}
      />
      <KpiTile
        label="Ort. 7 günlük uyum"
        value={pct(avg)}
        hint={`${scored.length} hastanın verisiyle`}
        icon={Activity}
        tone={avg !== null && avg < 0.6 ? "warning" : "default"}
      />
    </div>
  );
}

export function ClinicianPatientList() {
  const router = useRouter();
  const { data, error, loading, reload } = useAsync(() => api.patients(), []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Hastalar</h1>
        <p className="text-sm text-muted-foreground">
          Önce kritik uyarısı olanlar, ardından uyumu en düşük olanlar listelenir.
        </p>
      </div>

      {error && !data && <ErrorState message={error} onRetry={reload} />}

      {loading && !data && (
        <div className="space-y-3" role="status" aria-label="Hastalar yükleniyor">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
          <Skeleton className="h-64" />
        </div>
      )}

      {data && (
        <>
          <Kpis rows={data} />
          <Card className="py-0">
            {data.length === 0 ? (
              <EmptyState
                title="Henüz atanmış hasta yok"
                description="Hastalar sizi e-posta adresinizle hekim olarak seçtiğinde burada görünür."
              />
            ) : (
              <Table>
                <caption className="sr-only">Atanmış hastalar, uyum ve uyarı durumu</caption>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead className="pl-4">Hasta</TableHead>
                    <TableHead>7 gün uyum</TableHead>
                    <TableHead>30 gün uyum</TableHead>
                    <TableHead>Açık uyarı</TableHead>
                    <TableHead>Son ölçüm</TableHead>
                    <TableHead className="w-8">
                      <span className="sr-only">Detay</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((p) => {
                    const href = `/hekim/hasta/${p.id}`;
                    return (
                      <TableRow
                        key={p.id}
                        className={cn("cursor-pointer", p.critical_alerts > 0 && "bg-red-50/60 hover:bg-red-50")}
                        onClick={() => router.push(href)}
                      >
                        <TableCell className="pl-4 font-medium">
                          <Link
                            href={href}
                            className="rounded-sm hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {p.full_name}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <AdherenceBadge score={p.adherence_7d} band={p.band} />
                        </TableCell>
                        <TableCell>
                          <AdherenceBadge score={p.adherence_30d} band={bandOf(p.adherence_30d)} />
                        </TableCell>
                        <TableCell>
                          <span className="inline-flex items-center gap-2 tabular-nums">
                            {p.open_alerts}
                            {p.critical_alerts > 0 && (
                              <span className="inline-flex items-center gap-1 text-xs font-medium text-red-700">
                                <Flag className="size-3.5 fill-red-600 text-red-600" aria-hidden />
                                {p.critical_alerts} kritik
                              </span>
                            )}
                          </span>
                        </TableCell>
                        <TableCell
                          className="text-muted-foreground"
                          title={p.last_measurement_at ? new Date(p.last_measurement_at).toLocaleString("tr-TR") : undefined}
                        >
                          {relativeTime(p.last_measurement_at)}
                        </TableCell>
                        <TableCell>
                          <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
