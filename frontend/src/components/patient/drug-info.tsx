"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, Info, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { DrugLabel } from "@/lib/types";
import { cn } from "@/lib/utils";
import { errorMessage, errorStatus } from "./utils";

/** backend/app/services/openfda.py::parse_label çıktısı. */
interface DrugLabelInfo {
  query: string;
  resolved_generic: string;
  brand_names: string[];
  generic_names: string[];
  active_ingredients: string[];
  indications: string | null;
  warnings: string | null;
  drug_interactions: string | null;
  source_name: string;
  source_url: string | null;
  disclaimer: string | null;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

function parseLabel(raw: DrugLabel): DrugLabelInfo {
  return {
    query: str(raw.query) ?? "",
    resolved_generic: str(raw.resolved_generic) ?? "",
    brand_names: strings(raw.brand_names),
    generic_names: strings(raw.generic_names),
    active_ingredients: strings(raw.active_ingredients),
    indications: str(raw.indications),
    warnings: str(raw.warnings),
    drug_interactions: str(raw.drug_interactions),
    source_name: str(raw.source_name) ?? "openFDA Drug Label API",
    source_url: str(raw.source_url),
    disclaimer: str(raw.disclaimer),
  };
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: DrugLabelInfo }
  | { kind: "error"; message: string };

export function DrugInfoButton({ name, regionId }: { name: string; regionId: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>({ kind: "idle" });

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || state.kind === "ok" || state.kind === "loading") return;
    setState({ kind: "loading" });
    try {
      const raw = await api.drugLabel(name);
      setState({ kind: "ok", data: parseLabel(raw) });
    } catch (e) {
      const status = errorStatus(e);
      const message =
        status === 404
          ? `"${name}" için openFDA'da etiket bilgisi bulunamadı. Etken madde adıyla kaydetmeyi deneyebilirsiniz.`
          : status === 503
            ? "openFDA şu an yanıt vermiyor. Lütfen daha sonra tekrar deneyin."
            : status === 422
              ? "İlaç adı sorgu için çok kısa veya geçersiz."
              : errorMessage(e);
      setState({ kind: "error", message });
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" aria-expanded={open} aria-controls={regionId} onClick={() => void toggle()}>
        <Info /> Bilgi
        <ChevronDown className={cn("transition-transform", open && "rotate-180")} aria-hidden />
      </Button>
      {open && (
        <div id={regionId} role="region" aria-label={`${name} ilaç bilgisi`} className="basis-full">
          <DrugInfoPanel state={state} />
        </div>
      )}
    </>
  );
}

function DrugInfoPanel({ state }: { state: State }) {
  if (state.kind === "loading" || state.kind === "idle")
    return (
      <p role="status" className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> openFDA&apos;dan bilgi alınıyor…
      </p>
    );
  if (state.kind === "error")
    return <p className="mt-2 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">{state.message}</p>;

  const d = state.data;
  const ingredients = d.active_ingredients.length ? d.active_ingredients : d.generic_names;
  return (
    <div className="mt-2 space-y-3 rounded-lg border bg-muted/40 p-3 text-sm">
      <p className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
        Yalnızca eğitim amaçlıdır, tıbbi tavsiye değildir. Bilgiler ABD (FDA) ilaç etiketinden alınmış olup İngilizcedir;
        tedavi kararı için hekiminize veya eczacınıza danışın.
      </p>
      <dl className="grid gap-2">
        {ingredients.length > 0 && <Field label="Etken madde" value={ingredients.join(", ")} />}
        {d.brand_names.length > 0 && <Field label="ABD ticari adları" value={d.brand_names.join(", ")} />}
        {d.indications && <Field label="Kullanım alanı" value={d.indications} long />}
        {d.warnings && <Field label="Uyarılar" value={d.warnings} long />}
        {d.drug_interactions && <Field label="İlaç etkileşimleri" value={d.drug_interactions} long />}
      </dl>
      <p className="text-xs text-muted-foreground">
        Kaynak:{" "}
        {d.source_url ? (
          <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 underline">
            {d.source_name} <ExternalLink className="size-3" aria-hidden />
          </a>
        ) : (
          d.source_name
        )}
      </p>
      {d.disclaimer && <p className="text-xs text-muted-foreground">{d.disclaimer}</p>}
    </div>
  );
}

function Field({ label, value, long }: { label: string; value: string; long?: boolean }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={cn(long && "max-h-40 overflow-y-auto pr-1 text-xs leading-relaxed")} lang={long ? "en" : undefined}>
        {value}
      </dd>
    </div>
  );
}
