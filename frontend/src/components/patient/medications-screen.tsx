"use client";

import { useCallback, useState } from "react";
import { Clock, Pencil, Pill, Plus, PowerOff, RotateCcw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Medication, MedicationIn } from "@/lib/types";
import { DrugInfoButton } from "./drug-info";
import { MedicationForm } from "./medication-form";
import { ErrorState, LoadingState, PageHeader } from "./states";
import { useAsync } from "./use-async";
import { describeDays, errorMessage, hhmm } from "./utils";


type Editing = { mode: "new" } | { mode: "edit"; id: number } | null;

export function MedicationsScreen({ patientId }: { patientId: number }) {
  const meds = useAsync(useCallback(() => api.medications(patientId, true), [patientId]));
  const [editing, setEditing] = useState<Editing>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const replace = (m: Medication) =>
    meds.setData((list) => {
      if (!list) return [m];
      return list.some((x) => x.id === m.id) ? list.map((x) => (x.id === m.id ? m : x)) : [...list, m];
    });

  async function save(data: MedicationIn) {
    setSaving(true);
    setFormError(null);
    try {
      const saved =
        editing?.mode === "edit"
          ? await api.updateMedication(patientId, editing.id, data)
          : await api.createMedication(patientId, data);
      replace(saved);
      setEditing(null);
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function setActive(med: Medication, active: boolean) {
    if (!active && !window.confirm(`${med.name} pasife alınsın mı? Geçmiş kayıtlar korunur.`)) return;
    setBusyId(med.id);
    setRowError(null);
    try {
      if (active) {
        replace(await api.updateMedication(patientId, med.id, { active: true }));
      } else {
        await api.deactivateMedication(patientId, med.id);
        replace({ ...med, active: false });
      }
    } catch (e) {
      setRowError(`${med.name}: ${errorMessage(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  const active = (meds.data ?? []).filter((m) => m.active).sort((a, b) => a.name.localeCompare(b.name, "tr"));
  const inactive = (meds.data ?? []).filter((m) => !m.active);

  function startEdit(next: Editing) {
    setFormError(null);
    setEditing(next);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <PageHeader title="İlaçlar" description="İlaç planınız ve saatleri" />
        {editing?.mode !== "new" && (
          <Button onClick={() => startEdit({ mode: "new" })} className="h-10">
            <Plus /> İlaç ekle
          </Button>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        Dozu yalnızca hekiminizin yazdığı şekilde girin. Uygulama doz önermez veya değiştirmez.
      </p>

      {editing?.mode === "new" && (
        <Card>
          <CardHeader>
            <CardTitle>Yeni ilaç</CardTitle>
          </CardHeader>
          <CardContent>
            <MedicationForm saving={saving} error={formError} onSubmit={(d) => void save(d)} onCancel={() => setEditing(null)} />
          </CardContent>
        </Card>
      )}

      {rowError && <ErrorState message={rowError} />}
      {meds.loading && !meds.data && <LoadingState label="İlaçlar yükleniyor…" />}
      {meds.error && <ErrorState message={meds.error} onRetry={meds.reload} />}
      {meds.data && active.length === 0 && editing?.mode !== "new" && (
        <Card>
          <CardContent className="text-sm text-muted-foreground">Henüz aktif ilaç yok. “İlaç ekle” ile başlayın.</CardContent>
        </Card>
      )}

      <ul className="space-y-3">
        {active.map((m) => (
          <li key={m.id}>
            <Card>
              {editing?.mode === "edit" && editing.id === m.id ? (
                <CardContent>
                  <MedicationForm
                    medication={m}
                    saving={saving}
                    error={formError}
                    onSubmit={(d) => void save(d)}
                    onCancel={() => setEditing(null)}
                  />
                </CardContent>
              ) : (
                <CardContent className="space-y-3">
                  <div className="flex items-start gap-3">
                    <Pill className="mt-0.5 size-5 shrink-0 text-teal-700" aria-hidden />
                    <div className="flex-1">
                      <p className="text-base font-medium">
                        {m.name}
                        {m.dose_text && <span className="font-normal text-muted-foreground"> · {m.dose_text}</span>}
                      </p>
                      {m.active_ingredient && <p className="text-sm text-muted-foreground">Etken madde: {m.active_ingredient}</p>}
                      {m.notes && <p className="text-sm text-muted-foreground">{m.notes}</p>}
                      {!m.verified && (
                        <p className="mt-1 flex items-center gap-1 text-xs text-amber-700">
                          <TriangleAlert className="size-3.5" aria-hidden /> İlaç listemizde yok; hekiminiz kontrol edecek.
                        </p>
                      )}
                    </div>
                  </div>
                  {m.schedules.length > 0 ? (
                    <ul className="flex flex-wrap gap-1.5" aria-label="Saatler">
                      {[...m.schedules]
                        .sort((a, b) => a.time_of_day.localeCompare(b.time_of_day))
                        .map((s) => (
                          <li key={s.id} className="flex items-center gap-1 rounded-full bg-teal-50 px-2.5 py-1 text-xs text-teal-900">
                            <Clock className="size-3" aria-hidden />
                            {hhmm(s.time_of_day)} · {describeDays(s.days_of_week)}
                          </li>
                        ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-amber-700">Saat planı yok — hatırlatma ve uyum takibi için saat ekleyin.</p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <DrugInfoButton name={m.active_ingredient || m.name} regionId={`drug-info-${m.id}`} />
                    <Button variant="outline" size="sm" onClick={() => startEdit({ mode: "edit", id: m.id })}>
                      <Pencil /> Düzenle
                    </Button>
                    <Button variant="destructive" size="sm" disabled={busyId === m.id} onClick={() => void setActive(m, false)}>
                      <PowerOff /> Pasife al
                    </Button>
                  </div>
                </CardContent>
              )}
            </Card>
          </li>
        ))}
      </ul>

      {inactive.length > 0 && (
        <details className="rounded-xl bg-card p-3 text-sm ring-1 ring-foreground/10">
          <summary className="cursor-pointer font-medium">Pasif ilaçlar ({inactive.length})</summary>
          <ul className="mt-2 divide-y">
            {inactive.map((m) => (
              <li key={m.id} className="flex items-center justify-between gap-2 py-2">
                <span className="text-muted-foreground">
                  {m.name}
                  {m.dose_text ? ` · ${m.dose_text}` : ""}
                </span>
                <Button variant="outline" size="sm" disabled={busyId === m.id} onClick={() => void setActive(m, true)}>
                  <RotateCcw /> Yeniden etkinleştir
                </Button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
