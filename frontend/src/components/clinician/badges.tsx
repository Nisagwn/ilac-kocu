import { Eye, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Band, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BAND_CLASS, BAND_LABEL, SEVERITY_LABEL, pct } from "./format";

/** Uyum yüzdesi + renk bandı. Renk tek başına anlam taşımasın diye etiket de yazılır. */
export function AdherenceBadge({ score, band, className }: { score: number | null; band: Band; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("tabular-nums", BAND_CLASS[band], className)}
      aria-label={score === null ? "Uyum verisi yok" : `Uyum ${pct(score)}, ${BAND_LABEL[band]}`}
    >
      {score === null ? BAND_LABEL.veri_yok : `${pct(score)} · ${BAND_LABEL[band]}`}
    </Badge>
  );
}

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-red-200 bg-red-50 text-red-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  info: "border-sky-200 bg-sky-50 text-sky-800",
};

export const SEVERITY_ICON = { critical: OctagonAlert, warning: TriangleAlert, info: Info } as const;

export function SeverityBadge({ severity }: { severity: Severity }) {
  const Icon = SEVERITY_ICON[severity];
  return (
    <Badge variant="outline" className={SEVERITY_CLASS[severity]}>
      <Icon aria-hidden />
      {SEVERITY_LABEL[severity]}
    </Badge>
  );
}

export function ReadOnlyBadge() {
  return (
    <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-800">
      <Eye aria-hidden />
      Salt okunur
    </Badge>
  );
}
