"use client";

import { useState } from "react";
import { CircleCheck, Plus, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { DrugSearchHit, Medication, MedicationIn } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DrugCombobox } from "./drug-combobox";
import { WEEKDAYS, hhmm } from "./utils";

const ALL_DAYS = "1234567";

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
  medication?: Medication;
  saving: boolean;
  error: string | null;
  onSubmit: (data: MedicationIn) => void;
  onCancel: () => void;
}) {
  const idp = medication ? `med-${medication.id}` : "med-new";
  const [name, setName] = useState(medication?.name ?? "");
  const [doseText, setDoseText] = useState(medication?.dose_text ?? "");
  const [ingredient, setIngredient] = useState(medication?.active_ingredient ?? "");
  const [notes, setNotes] = useState(medication?.notes ?? "");
  // Katalogdan seçilen kayıt; ad elle değiştirilince bağlantı kalkar (sunucu birebir ad eşleşmesini yine dener)
  const [catalog, setCatalog] = useState<{ id: number; generic: string } | null>(() =>
    medication?.catalog_id ? { id: medication.catalog_id, generic: medication.active_ingredient ?? "" } : null,
  );
  const [times, setTimes] = useState<string[]>(() => {
    const t = [...new Set(medication?.schedules.map((s) => hhmm(s.time_of_day)) ?? [])].sort();
    return t.length ? t : ["08:00"];
  });
  const [days, setDays] = useState(() => initialDays(medication));
  const [localError, setLocalError] = useState<string | null>(null);
  const mixedDays = medication ? new Set(medication.schedules.map((s) => s.days_of_week)).size > 1 : false;

  function pickDrug(hit: DrugSearchHit) {
    setName(hit.brand ?? hit.generic_name);
    setIngredient(hit.generic_name);
    setCatalog({ id: hit.id, generic: hit.generic_name });
  }

  function toggleDay(d: string) {
    setDays((cur) => (cur.includes(d) ? cur.replace(d, "") : [...cur, d].sort().join("")));
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setLocalError(null);
    const cleanTimes = [...new Set(times.filter(Boolean))].sort();
    if (!name.trim()) return setLocalError("İlaç adı gerekli.");
    if (cleanTimes.length === 0) return setLocalError("En az bir saat ekleyin.");
    if (!days) return setLocalError("En az bir gün seçin.");
    onSubmit({
      name: name.trim(),
      dose_text: doseText.trim() || null,
      active_ingredient: ingredient.trim() || null,
      notes: notes.trim() || null,
      catalog_id: catalog?.id ?? null,
      schedules: cleanTimes.map((t) => ({ time_of_day: t, days_of_week: days })),
    });
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label={medication ? `${medication.name} düzenle` : "Yeni ilaç"}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-name`}>İlaç adı *</Label>
          <DrugCombobox
            id={`${idp}-name`}
            value={name}
            onChange={(v) => {
              setName(v);
              setCatalog(null);
            }}
            onSelect={pickDrug}
          />
          {catalog ? (
            <p className="flex items-center gap-1 text-xs text-teal-800">
              <CircleCheck className="size-3.5" aria-hidden /> Listeden seçildi{catalog.generic ? ` · ${catalog.generic}` : ""}
            </p>
          ) : (
            name.trim().length >= 2 && (
              <p className="flex items-center gap-1 text-xs text-amber-700">
                <TriangleAlert className="size-3.5" aria-hidden /> Listeden seçmezseniz de kaydedebilirsiniz; listede yoksa hekiminiz kontrol eder.
              </p>
            )
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-dose`}>Doz (hekiminizin yazdığı)</Label>
          <Input id={`${idp}-dose`} className="h-10" value={doseText} maxLength={120} onChange={(e) => setDoseText(e.target.value)} placeholder="ör. 1000 mg, 1 tablet" />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor={`${idp}-ing`}>Etken madde</Label>
          <Input id={`${idp}-ing`} className="h-10" value={ingredient} onChange={(e) => setIngredient(e.target.value)} placeholder="ör. metformin" />
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
