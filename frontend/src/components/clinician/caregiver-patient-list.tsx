"use client";

import Link from "next/link";
import { Bell, ChevronRight, Clock, Flag } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AdherenceBadge, ReadOnlyBadge } from "./badges";
import { bandOf, relativeTime } from "./format";
import { EmptyState, ErrorState, Skeleton } from "./states";
import { useAsync } from "./use-async";

export function CaregiverPatientList() {
  const { data, error, loading, reload } = useAsync(() => api.patients(), []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1">
          <h1 className="text-xl font-semibold">Hastalarım</h1>
          <p className="text-sm text-muted-foreground">
            Size bağlanan yakınlarınızın ilaç uyumu ve ölçümleri. Bu ekranda değişiklik yapılamaz.
          </p>
        </div>
        <ReadOnlyBadge />
      </div>

      {error && !data && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" role="status" aria-label="Hastalar yükleniyor">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      )}

      {data && data.length === 0 && (
        <Card>
          <EmptyState
            title="Bağlı hasta yok"
            description="Hasta, uygulamadan e-posta adresinizi bakım veren olarak eklediğinde burada görünür."
          />
        </Card>
      )}

      {data && data.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((p) => (
            <li key={p.id}>
              <Link
                href={`/yakin/hasta/${p.id}`}
                className="group block rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <Card className={cn("h-full transition-shadow group-hover:shadow-md", p.critical_alerts > 0 && "ring-red-300")}>
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between gap-2">
                      {p.full_name}
                      <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                      <div className="space-y-1">
                        <p>7 gün uyum</p>
                        <AdherenceBadge score={p.adherence_7d} band={p.band} />
                      </div>
                      <div className="space-y-1">
                        <p>30 gün uyum</p>
                        <AdherenceBadge score={p.adherence_30d} band={bandOf(p.adherence_30d)} />
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <Bell className="size-3.5" aria-hidden /> {p.open_alerts} açık uyarı
                      </span>
                      {p.critical_alerts > 0 && (
                        <span className="inline-flex items-center gap-1 font-medium text-red-700">
                          <Flag className="size-3.5 fill-red-600 text-red-600" aria-hidden /> {p.critical_alerts} kritik
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1">
                        <Clock className="size-3.5" aria-hidden /> Son ölçüm: {relativeTime(p.last_measurement_at)}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
