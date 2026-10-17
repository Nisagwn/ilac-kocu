"use client";

import { useState } from "react";
import { Check, ExternalLink, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { insightsApi } from "@/lib/api";
import type { EducationContent, ReviewStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { errorMessage, formatDateTime } from "./format";
import { EmptyState, ErrorState, LoadingState } from "./states";
import { useAsync } from "./use-async";

const TABS: { status: ReviewStatus; label: string }[] = [
  { status: "pending", label: "Onay bekleyen" },
  { status: "approved", label: "Onaylanan" },
  { status: "rejected", label: "Reddedilen" },
];

const STATUS_BADGE: Record<ReviewStatus, { label: string; className: string }> = {
  pending: { label: "Onay bekliyor", className: "border-amber-200 bg-amber-50 text-amber-800" },
  approved: { label: "Onaylı", className: "border-emerald-200 bg-emerald-50 text-emerald-800" },
  rejected: { label: "Reddedildi", className: "border-red-200 bg-red-50 text-red-800" },
};

function ContentItem({
  item,
  onDecided,
}: {
  item: EducationContent;
  onDecided: (updated: EducationContent) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const badge = STATUS_BADGE[item.review_status];

  async function decide(kind: "approve" | "reject") {
    setBusy(kind);
    setError(null);
    try {
      const updated =
        kind === "approve" ? await insightsApi.approveEducation(item.id) : await insightsApi.rejectEducation(item.id);
      onDecided(updated);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const titleId = `edu-${item.id}-title`;
  return (
    <li>
      <Card size="sm" aria-labelledby={titleId}>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <h2 id={titleId} className="font-medium break-words">
                {item.title}
              </h2>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <Badge variant="outline" className={badge.className}>
                  {badge.label}
                </Badge>
                <Badge variant="outline">{item.kind === "openfda" ? "openFDA (otomatik)" : "Kürate"}</Badge>
                {item.tags && <span className="break-all">#{item.tags.split(",").join(" #")}</span>}
              </div>
            </div>
            {item.reviewed_at && (
              <p className="text-xs text-muted-foreground">Karar: {formatDateTime(item.reviewed_at)}</p>
            )}
          </div>

          <details className="group rounded-md border bg-muted/30 p-2 text-sm">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">İçeriği önizle</summary>
            <p className="mt-2 whitespace-pre-line break-words">{item.body}</p>
          </details>

          <p className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            Kaynak: {item.source_name}
            {item.source_url && (
              <a
                href={item.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 text-teal-700 underline-offset-2 hover:underline"
              >
                bağlantı <ExternalLink className="size-3" aria-hidden />
                <span className="sr-only">(yeni sekmede açılır)</span>
              </a>
            )}
          </p>

          {error && <ErrorState message={error} />}

          <div className="flex flex-wrap gap-2">
            {item.review_status !== "approved" && (
              <Button size="sm" onClick={() => decide("approve")} disabled={busy !== null} aria-describedby={titleId}>
                <Check aria-hidden /> {busy === "approve" ? "Onaylanıyor…" : "Onayla"}
              </Button>
            )}
            {item.review_status !== "rejected" && (
              <Button
                size="sm"
                variant="destructive"
                onClick={() => decide("reject")}
                disabled={busy !== null}
                aria-describedby={titleId}
              >
                <X aria-hidden /> {busy === "reject" ? "Reddediliyor…" : "Reddet"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

export function EducationReview() {
  const [status, setStatus] = useState<ReviewStatus>("pending");
  const [notice, setNotice] = useState<string | null>(null);
  const { data, error, loading, reload, setData } = useAsync(() => insightsApi.education(status), [status]);

  function handleDecided(updated: EducationContent) {
    // Karar verilen içerik bu sekmeden çıkar
    setData((prev) => (prev ? prev.filter((i) => i.id !== updated.id) : prev));
    setNotice(
      `"${updated.title}" ${updated.review_status === "approved" ? "onaylandı" : "reddedildi; koç artık bu içeriği kaynak göstermez"}.`,
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Eğitim içeriği</h1>
        <p className="text-sm text-muted-foreground">
          Koçun kaynak gösterdiği içerikleri gözden geçirin. Reddedilen içerik koç tarafından kullanılmaz; onay
          bekleyen openFDA içeriği hastaya &quot;hekim onayı bekliyor&quot; notuyla gösterilir.
        </p>
      </div>

      <div role="tablist" aria-label="Onay durumu" className="flex flex-wrap gap-1 rounded-lg bg-muted p-1">
        {TABS.map((t) => (
          <button
            key={t.status}
            role="tab"
            type="button"
            aria-selected={status === t.status}
            onClick={() => {
              setStatus(t.status);
              setNotice(null);
            }}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              status === t.status && "bg-background font-medium text-foreground shadow-sm",
            )}
          >
            {t.label}
            {status === t.status && data && <span className="ml-1 tabular-nums">({data.length})</span>}
          </button>
        ))}
      </div>

      <p aria-live="polite" className={cn("text-sm text-emerald-800", !notice && "sr-only")}>
        {notice}
      </p>

      <div role="tabpanel">
        {error && !data && <ErrorState message={error} onRetry={reload} />}
        {loading && !data && <LoadingState label="İçerikler yükleniyor…" />}
        {data && data.length === 0 && (
          <EmptyState
            title={status === "pending" ? "Onay bekleyen içerik yok" : "Bu durumda içerik yok"}
            description={status === "pending" ? "Yeni openFDA içerikleri hastalar ilaç ekledikçe burada görünür." : undefined}
          />
        )}
        {data && data.length > 0 && (
          <ul className="space-y-3">
            {data.map((item) => (
              <ContentItem key={item.id} item={item} onDecided={handleDecided} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
