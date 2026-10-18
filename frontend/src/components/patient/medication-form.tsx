"use client";

import { useState } from "react";
import { BadgeCheck, Plus, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { Medication, MedicationCatalogFields, MedicationIn } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DrugCombobox, type DrugChoice } from "./drug-combobox";
import { WEEKDAYS, hhmm } from "./utils";

const ALL_DAYS = "1234567";

/** İlaç adının katalogla ilişkisi: listeden seçildi / bilerek listede olmayan ilaç / henüz seçim yok. */
type CatalogLink =
  | { kind: "catalog"; id: number; pharmClass: string | null }
  | { kind: "unlisted" }
  | { kind: "none" };

function initialLink(med?: Medication & MedicationCatalogFields): CatalogLink {
  if (!med) return { kind: "none" };
  return med.catalog_id ? { kind: "catalog", id: med.catalog_id, pharmClass: null } : { kind: "unlisted" };
}

function initialDays(med?: Medication): string {
  if (!med || med.schedules.length === 0) return ALL_DAYS;
  const union = new Set(med.schedules.flatMap((s) => s.days_of_week.split("")));
  return [...union].sort().join("");
}

export function MedicationForm({
  medication,
  saving,
  error,
  onSubmit,
  onCancel,
}: {
  medication?: Medication & MedicationCatalogFields;
  saving: boolean;
  error: string | null;
  onSubmit: (data: MedicationIn & MedicationCatalogFields) => void;
  onCancel: () => void;
}) {
  const idp = medication ? `med-${medication.id}` : "med-new";
  const [name, setName] = useState(medication?.name ?? "");
  const [doseText, setDoseText] = useState(medication?.dose_text ?? "");
  const [ingredient, setIngredient] = useState(medication?.active_ingredient ?? "");
  const [notes, setNotes] = useState(medication?.notes ?? "");
  const [times, setTimes] = useState<string[]>(() => {
    const t = [...new Set(medication?.schedules.map((s) => hhmm(s.time_of_day)) ?? [])].sort();
    return t.length ? t : ["08:00"];
  });
  const [days, setDays] = useState(() => initialDays(medication));
  const [localError, setLocalError] = useState<string | null>(null);
  const [link, setLink] = useState<CatalogLink>(() => initialLink(medication));
  const mixedDays = medication ? new Set(medication.schedules.map((s) => s.days_of_week)).size > 1 : false;

  function toggleDay(d: string) {
    setDays((cur) => (cur.includes(d) ? cur.replace(d, "") : [...cur, d].sort().join("")));
  }

  function typeName(text: string) {
    setName(text);
    if (link.kind === "catalog") setIngredient(""); // katalog bağı koptu: etken madde artık bilinmiyor
    if (link.kind !== "none") setLink({ kind: "none" });
  }

  function chooseDrug(choice: DrugChoice, label: string) {
    setLocalError(null);
    if (choice.kind === "catalog") {
      setName(label);
      setIngredient(choice.item.generic_name);
      setLink({ kind: "catalog", id: choice.item.id, pharmClass: choice.item.pharm_class });
    } else {
      setLink({ kind: "unlisted" });
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setLocalError(null);
    const cleanTimes = [...new Set(times.filter(Boolean))].sort();
    if (!name.trim()) return setLocalError("İlaç adı gerekli.");
    if (cleanTimes.length === 0) return setLocalError("En az bir saat ekleyin.");
    if (!days) return setLocalError("En az bir gün seçin.");
    if (link.kind === "none")
      return setLocalError("İlacı listeden seçin ya da “Listede yok, yine de ekle” seçeneğini kullanın.");
    onSubmit({
      catalog_id: link.kind === "catalog" ? link.id : null,
      name: name.trim(),
      dose_text: doseText.trim() || null,
      active_ingredient: ingredient.trim() || null,
      notes: notes.trim() || null,
      schedules: cleanTimes.map((t) => ({ time_of_day: t, days_of_week: days })),
    });
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label={medication ? `${medication.name} düzenle` : "Yeni ilaç"}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-name`}>İlaç adı *</Label>
          <DrugCombobox id={`${idp}-name`} value={name} onType={typeName} onChoose={chooseDrug} />
          {link.kind === "catalog" && (
            <p className="flex items-center gap-1 text-xs text-teal-800">
              <BadgeCheck className="size-3.5" aria-hidden /> Katalogdan seçildi
              {link.pharmClass && <span className="text-muted-foreground">· {link.pharmClass}</span>}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-dose`}>Doz (hekiminizin yazdığı)</Label>
          <Input id={`${idp}-dose`} className="h-10" value={doseText} maxLength={120} onChange={(e) => setDoseText(e.target.value)} placeholder="ör. 1000 mg, 1 tablet" />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor={`${idp}-ing`}>Etken madde</Label>
          <Input
            id={`${idp}-ing`}
            className={cn("h-10", link.kind === "catalog" && "bg-muted")}
            value={ingredient}
            readOnly={link.kind === "catalog"}
            aria-describedby={`${idp}-ing-hint`}
            onChange={(e) => setIngredient(e.target.value)}
            placeholder={link.kind === "catalog" ? "" : "ör. metformin"}
          />
          <p id={`${idp}-ing-hint`} className="text-xs text-muted-foreground">
            {link.kind === "catalog"
              ? "Katalogdan otomatik dolduruldu; değiştirmek için ilacı yeniden seçin."
              : "İlacı listeden seçtiğinizde otomatik dolar."}
          </p>
          {link.kind === "unlisted" && (
            <p className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              Doğrulanmamış ilaç — bilgi kartı gösterilemez. Hekiminiz bu ilacı listede “doğrulanmamış” olarak görür;
              adı ilaç kutusundaki gibi yazdığınızdan emin olun.
            </p>
          )}
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Saatler</legend>
        <div className="flex flex-wrap gap-2">
          {times.map((t, i) => (
            <div key={i} className="flex items-center gap-1">
              <Input
                type="time"
                aria-label={`${i + 1}. saat`}
                className="h-10 w-32"
                value={t}
                onChange={(e) => setTimes((cur) => cur.map((x, j) => (j === i ? e.target.value : x)))}
                required
              />
              {times.length > 1 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${t || `${i + 1}.`} saatini kaldır`}
                  onClick={() => setTimes((cur) => cur.filter((_, j) => j !== i))}
                >
                  <X />
                </Button>
              )}
            </div>
          ))}
          <Button type="button" variant="outline" className="h-10" onClick={() => setTimes((cur) => [...cur, "20:00"])}>
            <Plus /> Saat ekle
          </Button>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Günler</legend>
        <div className="flex flex-wrap gap-1.5">
          {WEEKDAYS.map((d) => {
            const on = days.includes(d.value);
            return (
              <button
                key={d.value}
                type="button"
                aria-pressed={on}
                aria-label={d.long}
                onClick={() => toggleDay(d.value)}
                className={cn(
                  "h-10 min-w-11 rounded-lg border px-2 text-sm font-medium transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  on ? "border-teal-700 bg-teal-700 text-white" : "border-input bg-background text-muted-foreground hover:bg-muted",
                )}
              >
                {d.short}
              </button>
            );
          })}
          <Button type="button" variant="ghost" className="h-10" onClick={() => setDays(ALL_DAYS)}>
            Her gün
          </Button>
        </div>
        {mixedDays && (
          <p className="text-xs text-muted-foreground">
            Bu ilacın saatleri farklı günlere planlanmıştı; kaydettiğinizde seçili günler tüm saatlere uygulanır.
          </p>
        )}
      </fieldset>

      <div className="space-y-1.5">
        <Label htmlFor={`${idp}-notes`}>Notlar</Label>
        <Textarea id={`${idp}-notes`} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="ör. kahvaltıyla birlikte" />
      </div>

      {(localError || error) && (
        <p role="alert" className="text-sm text-destructive">
          {localError ?? error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" className="h-10 flex-1" disabled={saving}>
          {saving ? "Kaydediliyor…" : "Kaydet"}
        </Button>
        <Button type="button" variant="outline" className="h-10" onClick={onCancel} disabled={saving}>
          Vazgeç
        </Button>
      </div>
    </form>
  );
}
