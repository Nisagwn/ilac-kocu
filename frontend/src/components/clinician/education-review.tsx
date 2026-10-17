"use client";

import { useState } from "react";
import { Check, ExternalLink, RotateCcw, RotateCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { EducationItem, ReviewStatus } from "@/lib/types";
import { errorMessage, formatDateTime } from "./format";
import { EmptyState, ErrorState, LoadingState } from "./states";
import { useAsync } from "./use-async";

const TABS: { status: ReviewStatus; label: string; empty: string }[] = [
  { status: "pending", label: "Onay bekleyen", empty: "Onay bekleyen içerik yok." },
  { status: "approved", label: "Onaylanan", empty: "Henüz onaylanan içerik yok." },
  { status: "rejected", label: "Reddedilen", empty: "Reddedilen içerik yok." },
];

const KIND_LABEL = { curated: "Kürate", openfda: "openFDA etiketi" } as const;

function ReviewCard({ item, onChange }: { item: EducationItem; onChange: (updated: EducationItem) => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteId = `reject-note-${item.id}`;

  async function run(fn: () => Promise<EducationItem>) {
    setBusy(true);
    setError(null);
    try {
      onChange(await fn());
      setRejecting(false);
      setNote("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card size="sm">
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-medium">{item.title}</h3>
            <p className="text-xs text-muted-foreground">
              {item.source_url ? (
                <a href={item.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                  {item.source_name} <ExternalLink className="size-3" aria-hidden />
                </a>
              ) : (
                item.source_name
              )}
              {item.tags && <> · {item.tags.split(",").join(", ")}</>}
            </p>
          </div>
          <Badge variant="outline">{KIND_LABEL[item.kind]}</Badge>
        </div>

        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Metni göster</summary>
          <p className="mt-2 whitespace-pre-line">{item.body}</p>
        </details>

        {item.review_status !== "pending" && (
          <p className="text-xs text-muted-foreground">
            {item.review_status === "approved" ? "Onaylayan" : "Reddeden"}: {item.reviewer_name ?? "—"}
            {item.reviewed_at && ` · ${formatDateTime(item.reviewed_at)}`}
            {item.review_note && <> · “{item.review_note}”</>}
          </p>
        )}

        {rejecting && (
          <form
            className="space-y-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              void run(() => api.rejectEducation(item.id, note.trim()));
            }}
          >
            <Label htmlFor={noteId}>Reddetme nedeni</Label>
            <Textarea
              id={noteId}
              value={note}
              maxLength={1000}
              onChange={(e) => setNote(e.target.value)}
              placeholder="ör. Kaynak güncel değil, Türkiye uygulamasıyla uyumsuz"
              required
              minLength={3}
            />
            <div className="flex gap-2">
              <Button type="submit" variant="destructive" size="sm" disabled={busy || note.trim().length < 3}>
                <X /> Reddet
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={() => setRejecting(false)} disabled={busy}>
                Vazgeç
              </Button>
            </div>
          </form>
        )}

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        {!rejecting && (
          <div className="flex flex-wrap gap-2">
            {item.review_status !== "approved" && (
              <Button size="sm" disabled={busy} onClick={() => void run(() => api.approveEducation(item.id))}>
                <Check /> Onayla
              </Button>
            )}
            {item.review_status !== "rejected" && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setRejecting(true)}>
                <X /> Reddet
              </Button>
            )}
            {item.review_status !== "pending" && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => api.resetEducation(item.id))}>
                <RotateCcw /> Kararı geri al
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Hekim: eğitim içeriği (RAG korpusu) onay ekranı. Reddedilen içerik koç tarafından kaynak gösterilmez. */
export function EducationReview() {
  const { data, error, loading, reload, setData } = useAsync(() => api.education(), []);
  const items = data ?? [];

  function replace(updated: EducationItem) {
    setData((list) => (list ? list.map((x) => (x.id === updated.id ? updated : x)) : list));
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Eğitim içeriği onayı</h1>
          <p className="text-sm text-muted-foreground">
            Koç yalnızca bu içeriklerden kaynak gösterir. Reddettiğiniz içerik koçun aramasından çıkar; onay bekleyen
            içerik kullanılmaya devam eder.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
          <RotateCw aria-hidden className={loading ? "animate-spin" : undefined} /> Yenile
        </Button>
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="İçerikler yükleniyor…" />}

      {data && (
        <Tabs defaultValue="pending">
          <TabsList>
            {TABS.map((t) => (
              <TabsTrigger key={t.status} value={t.status}>
                {t.label} ({items.filter((i) => i.review_status === t.status).length})
              </TabsTrigger>
            ))}
          </TabsList>
          {TABS.map((t) => {
            const list = items.filter((i) => i.review_status === t.status);
            return (
              <TabsContent key={t.status} value={t.status} className="pt-3">
                {list.length === 0 ? (
                  <Card>
                    <EmptyState title={t.empty} />
                  </Card>
                ) : (
                  <ul className="space-y-3">
                    {list.map((item) => (
                      <li key={item.id}>
                        <ReviewCard item={item} onChange={replace} />
                      </li>
                    ))}
                  </ul>
                )}
              </TabsContent>
            );
          })}
        </Tabs>
      )}
    </div>
  );
}
