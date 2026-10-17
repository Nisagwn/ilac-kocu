import { ArrowDownRight, ArrowRight, ArrowUpRight, Eye, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { AdherenceTrend, Band, Severity } from "@/lib/types";
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

const TREND = {
  improving: { Icon: ArrowUpRight, label: "arttı", className: "text-emerald-700" },
  stable: { Icon: ArrowRight, label: "değişmedi", className: "text-muted-foreground" },
  worsening: { Icon: ArrowDownRight, label: "düştü", className: "text-red-700" },
} as const;

/** 7 günlük uyumun önceki 7 güne göre yönü (±10 puan eşiği backend'de: services/clinical_insights.py). */
export function TrendIndicator({ trend, previous }: { trend: AdherenceTrend | null; previous: number | null }) {
  if (!trend) return null;
  const { Icon, label, className } = TREND[trend];
  const text = `Önceki 7 güne göre ${label}${previous !== null ? ` (önceki: ${pct(previous)})` : ""}`;
  return (
    <span className={cn("inline-flex items-center", className)} title={text}>
      <Icon className="size-4" aria-hidden />
      <span className="sr-only">{text}</span>
    </span>
  );
}

/** İlaç kataloğunda olmayan (hastanın elle yazdığı) ilaç: hekim adı ve etken maddeyi kontrol etmeli. */
export function UnverifiedDrugBadge() {
  return (
    <Badge
      variant="outline"
      className="ml-1 border-amber-200 bg-amber-50 text-amber-800"
      title="İlaç katalogda bulunamadı; adı ve etken maddeyi kontrol edin"
    >
      <TriangleAlert aria-hidden />
      Doğrulanmadı
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
