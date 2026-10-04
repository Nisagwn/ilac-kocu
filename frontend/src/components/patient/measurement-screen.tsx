"use client";

import { useCallback, useState } from "react";
import { AlertTriangle, CheckCircle2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Alert, MeasurementType } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BloodPressureChart, GlucoseChart } from "./measurement-charts";
import { NativeSelect } from "./native-select";
import { ErrorState, LoadingState, PageHeader } from "./states";
import { useAsync } from "./use-async";
import { MEASUREMENT_LABEL, MEASUREMENT_UNIT, errorMessage, formatDateTime } from "./utils";

const TYPES: MeasurementType[] = ["glucose_fasting", "glucose_postprandial", "blood_pressure"];

function isMeasurementType(v: string): v is MeasurementType {
  return (TYPES as string[]).includes(v);
}

export function MeasurementScreen({ patientId }: { patientId: number }) {
  const list = useAsync(useCallback(() => api.measurements(patientId, 30), [patientId]));
  const [deleting, setDeleting] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function remove(id: number) {
    if (!window.confirm("Bu ölçüm silinsin mi?")) return;
    setDeleting(id);
    setDeleteError(null);
    try {
      await api.deleteMeasurement(patientId, id);
      list.setData((d) => d?.filter((m) => m.id !== id) ?? d);
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeleting(null);
    }
  }

  const recent = [...(list.data ?? [])].reverse().slice(0, 15);

  return (
    <div className="space-y-4">
      <PageHeader title="Ölçüm" description="Kan şekeri ve tansiyon değerlerinizi kaydedin" />

      <MeasurementForm patientId={patientId} onSaved={list.reload} />

      {list.error && <ErrorState message={list.error} onRetry={list.reload} />}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Kan şekeri (mg/dL)</CardTitle>
            <CardDescription>Son 30 gün</CardDescription>
          </CardHeader>
          <CardContent>{list.data ? <GlucoseChart data={list.data} /> : !list.error && <LoadingState />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Tansiyon (mmHg)</CardTitle>
            <CardDescription>Son 30 gün</CardDescription>
          </CardHeader>
          <CardContent>
            {list.data ? <BloodPressureChart data={list.data} /> : !list.error && <LoadingState />}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Son ölçümler</CardTitle>
        </CardHeader>
        <CardContent>
          {deleteError && <ErrorState message={deleteError} />}
          {list.data && recent.length === 0 && <p className="text-sm text-muted-foreground">Henüz ölçüm yok.</p>}
          <ul className="divide-y">
            {recent.map((m) => (
              <li key={m.id} className="flex items-center gap-3 py-2">
                <div className="flex-1">
                  <p className="font-medium tabular-nums">
                    {m.type === "blood_pressure" ? `${m.value}/${m.value2 ?? "—"}` : m.value} {m.unit}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {MEASUREMENT_LABEL[m.type]} · {formatDateTime(m.measured_at)}
                    {m.note ? ` · ${m.note}` : ""}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`${formatDateTime(m.measured_at)} tarihli ölçümü sil`}
                  disabled={deleting === m.id}
                  onClick={() => void remove(m.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function MeasurementForm({ patientId, onSaved }: { patientId: number; onSaved: () => void }) {
  const [type, setType] = useState<MeasurementType>("glucose_fasting");
  const [value, setValue] = useState("");
  const [value2, setValue2] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ alert: Alert | null; text: string } | null>(null);
  const isBp = type === "blood_pressure";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    const v = Number(value.replace(",", "."));
    const v2 = Number(value2.replace(",", "."));
    if (!Number.isFinite(v) || v <= 0) return setError("Geçerli bir değer girin.");
    if (isBp && (!Number.isFinite(v2) || v2 <= 0)) return setError("Tansiyon için küçük (diyastolik) değeri de girin.");
    setSaving(true);
    try {
      const created = await api.createMeasurement(patientId, {
        type,
        value: v,
        value2: isBp ? v2 : null,
        note: note.trim() || null,
      });
      setResult({
        alert: created.alert,
        text: `${MEASUREMENT_LABEL[type]}: ${isBp ? `${v}/${v2}` : v} ${MEASUREMENT_UNIT[type]} kaydedildi.`,
      });
      setValue("");
      setValue2("");
      setNote("");
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Yeni ölçüm</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {result?.alert && <MeasurementAlert alert={result.alert} />}
        {result && !result.alert && (
          <p role="status" className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            <CheckCircle2 className="size-4" aria-hidden /> {result.text}
          </p>
        )}
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="m-type">Ölçüm türü</Label>
            <NativeSelect
              id="m-type"
              value={type}
              onChange={(e) => {
                if (isMeasurementType(e.target.value)) setType(e.target.value);
              }}
            >
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {MEASUREMENT_LABEL[t]}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className={cn("grid gap-3", isBp && "grid-cols-2")}>
            <div className="space-y-1.5">
              <Label htmlFor="m-value">{isBp ? "Büyük (sistolik)" : "Değer"} ({MEASUREMENT_UNIT[type]})</Label>
              <Input
                id="m-value"
                inputMode="decimal"
                className="h-10"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={isBp ? "ör. 125" : "ör. 110"}
                required
              />
            </div>
            {isBp && (
              <div className="space-y-1.5">
                <Label htmlFor="m-value2">Küçük (diyastolik) (mmHg)</Label>
                <Input
                  id="m-value2"
                  inputMode="decimal"
                  className="h-10"
                  value={value2}
                  onChange={(e) => setValue2(e.target.value)}
                  placeholder="ör. 80"
                  required
                />
              </div>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="m-note">Not (isteğe bağlı)</Label>
            <Input
              id="m-note"
              className="h-10"
              maxLength={255}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="ör. kahvaltıdan 2 saat sonra"
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" className="h-11 w-full text-base" disabled={saving}>
            {saving ? "Kaydediliyor…" : "Kaydet"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function MeasurementAlert({ alert }: { alert: Alert }) {
  const critical = alert.severity === "critical";
  return (
    <div
      role="alert"
      className={cn(
        "flex gap-3 rounded-lg border-2 px-3 py-3",
        critical ? "border-red-500 bg-red-50 text-red-900" : "border-amber-400 bg-amber-50 text-amber-900",
      )}
    >
      <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="space-y-1 text-sm">
        <p className="font-semibold">{critical ? "Kritik değer" : "Hedef aralık dışında"}</p>
        <p>{alert.message}</p>
        <p>
          {critical ? (
            <>
              Kendinizi kötü hissediyorsanız hemen{" "}
              <a href="tel:112" className="font-semibold underline">
                112&apos;yi arayın
              </a>
              . Bu değer hekiminizin paneline uyarı olarak düştü.
            </>
          ) : (
            "Bu değer hekiminizin paneline uyarı olarak düştü. Değer tekrarlarsa hekiminize danışın."
          )}
        </p>
      </div>
    </div>
  );
}
