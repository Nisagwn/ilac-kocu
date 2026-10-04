"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SEVERITY_ICON, SeverityBadge } from "./badges";
import { errorMessage, formatDateTime, relativeTime } from "./format";

const KIND_LABEL: Record<string, string> = {
  emergency_symptom: "Acil belirti",
  out_of_range: "Hedef dışı ölçüm",
  low_adherence: "Düşük uyum",
};

const BORDER: Record<Alert["severity"], string> = {
  critical: "border-l-red-500",
  warning: "border-l-amber-500",
  info: "border-l-sky-500",
};

/**
 * Tek uyarı satırı. `onAcked` verilmezse (yakın / salt okunur) "Okundu" düğmesi gösterilmez.
 */
export function AlertItem({
  alert,
  patientName,
  patientHref,
  onAcked,
}: {
  alert: Alert;
  patientName?: string;
  patientHref?: string;
  onAcked?: (updated: Alert) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const Icon = SEVERITY_ICON[alert.severity];
  const acked = alert.acknowledged_at !== null;

  async function ack() {
    setBusy(true);
    setError(null);
    try {
      onAcked?.(await api.ackAlert(alert.id));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      className={cn(
        "flex flex-col gap-2 rounded-lg border border-l-4 bg-card p-3 sm:flex-row sm:items-start",
        BORDER[alert.severity],
        acked && "opacity-60",
      )}
    >
      <Icon
        aria-hidden
        className={cn(
          "mt-0.5 hidden size-4 shrink-0 sm:block",
          alert.severity === "critical" && "text-red-600",
          alert.severity === "warning" && "text-amber-600",
          alert.severity === "info" && "text-sky-600",
        )}
      />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={alert.severity} />
          <span className="text-xs text-muted-foreground">{KIND_LABEL[alert.kind] ?? alert.kind}</span>
          {patientName &&
            (patientHref ? (
              <Link href={patientHref} className="inline-flex items-center text-sm font-medium hover:underline">
                {patientName}
                <ChevronRight className="size-3.5" aria-hidden />
              </Link>
            ) : (
              <span className="text-sm font-medium">{patientName}</span>
            ))}
        </div>
        <p className="text-sm break-words">{alert.message}</p>
        <p className="text-xs text-muted-foreground">
          <time dateTime={alert.created_at} title={formatDateTime(alert.created_at)}>
            {relativeTime(alert.created_at)}
          </time>
          {acked && alert.acknowledged_at && <> · Okundu: {formatDateTime(alert.acknowledged_at)}</>}
        </p>
        {error && (
          <p role="alert" className="text-xs text-red-700">
            {error}
          </p>
        )}
      </div>
      {onAcked && !acked && (
        <Button
          variant="outline"
          size="sm"
          onClick={ack}
          disabled={busy}
          aria-label={`Uyarıyı okundu olarak işaretle: ${alert.message}`}
          className="self-start"
        >
          {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
          Okundu
        </Button>
      )}
    </li>
  );
}
