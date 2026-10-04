"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMe } from "@/hooks/use-me";

export function LoadingState({ label = "Yükleniyor…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-800">
      <AlertCircle className="size-4 shrink-0" aria-hidden />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Tekrar dene
        </Button>
      )}
    </div>
  );
}

export function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}

/**
 * Oturumdaki hastanın kimliğini çözer; hazır olana kadar yükleniyor/hata durumunu gösterir.
 * Sayfalar içeriği `children(patientId)` ile çizer.
 */
export function PatientGate({ children }: { children: (patientId: number) => React.ReactNode }) {
  const { me, error } = useMe();
  if (error) return <ErrorState message={error} />;
  if (!me) return <LoadingState />;
  if (me.patient_id == null) return <ErrorState message="Bu ekran yalnızca hasta hesapları içindir." />;
  return <>{children(me.patient_id)}</>;
}
