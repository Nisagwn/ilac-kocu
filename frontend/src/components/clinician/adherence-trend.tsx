import type { TrendDirection } from "@/lib/types";
import { cn } from "@/lib/utils";

const SYMBOL: Record<TrendDirection, string> = { up: "▲", down: "▼", flat: "=" };
const WORD: Record<TrendDirection, string> = { up: "artış", down: "düşüş", flat: "belirgin değişim yok" };

/**
 * Haftalık uyum eğilimi: bu hafta − geçen hafta (yüzde puan), ör. "▲ +12".
 * Yön yalnızca renge dayanmaz; sembol ve ekran okuyucu metni de verilir.
 */
export function AdherenceTrendIndicator({
  delta,
  direction,
  className,
}: {
  delta: number | null | undefined;
  direction: TrendDirection | null | undefined;
  className?: string;
}) {
  if (delta === null || delta === undefined || !direction) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)} title="Karşılaştırma için iki haftalık veri yok">
        <span aria-hidden>—</span>
        <span className="sr-only">Haftalık eğilim için yeterli veri yok</span>
      </span>
    );
  }
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  const label = `Geçen haftaya göre ${sign}${Math.abs(delta)} puan, ${WORD[direction]}`;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-xs font-medium tabular-nums",
        direction === "up" && "text-emerald-700",
        direction === "down" && "text-red-700",
        direction === "flat" && "text-muted-foreground",
        className,
      )}
      title={label}
    >
      <span aria-hidden>
        {SYMBOL[direction]} {sign}
        {Math.abs(delta)}
      </span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
