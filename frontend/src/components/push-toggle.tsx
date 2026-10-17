"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, BellRing, Smartphone, TriangleAlert } from "lucide-react";
import { ApiError, request } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// Geliştirme modunda SW, hash'siz /_next/static dosyalarını önbelleğe almasın diye ?dev=1 ile kaydedilir.
const SW_URL = process.env.NODE_ENV === "development" ? "/sw.js?dev=1" : "/sw.js";

/** Kök layout'a bir kez yerleştirilir; service worker'ı kaydeder. Görsel çıktısı yoktur. */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register(SW_URL, { scope: "/" }).catch((err) => {
      console.warn("Service worker kaydedilemedi", err);
    });
  }, []);
  return null;
}

type Status =
  | "loading"
  | "unsupported" // tarayıcı Push API desteklemiyor
  | "ios-install" // iOS: ana ekrana eklenmeden push yok
  | "denied" // kullanıcı bildirim iznini reddetmiş
  | "server-off" // backend VAPID yapılandırılmamış (503)
  | "off"
  | "on";

function isIos() {
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
}

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlBase64ToUint8Array(base64: string) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function getRegistration() {
  const existing = await navigator.serviceWorker.getRegistration("/");
  if (!existing) await navigator.serviceWorker.register(SW_URL, { scope: "/" });
  return navigator.serviceWorker.ready;
}

async function fetchVapidKey(): Promise<string> {
  const { public_key } = await request<{ public_key: string }>("/push/vapid-public-key");
  return public_key;
}

function errorMessage(err: unknown) {
  if (err instanceof ApiError) return err.message;
  if (err instanceof DOMException && err.name === "NotAllowedError") return "Bildirim izni verilmedi.";
  return "Bir hata oluştu, lütfen tekrar deneyin.";
}

const TEXT = {
  patient: {
    title: "İlaç hatırlatmaları",
    description: "İlaç saatiniz geldiğinde bu cihaza bildirim gönderilir. Bildirimde sağlık verisi yer almaz.",
    on: "İlaç hatırlatmalarını aç",
    off: "İlaç hatırlatmalarını kapat",
  },
  clinician: {
    title: "Hekim bildirimleri",
    description:
      "Haftalık hasta özetleri hazır olduğunda (Pazartesi sabahı) bu cihaza bildirim gönderilir. Bildirimde hasta adı veya sağlık verisi yer almaz.",
    on: "Bildirimleri aç",
    off: "Bildirimleri kapat",
  },
} as const;

export function PushToggle({ className, audience = "patient" }: { className?: string; audience?: keyof typeof TEXT }) {
  const text = TEXT[audience];
  const [status, setStatus] = useState<Status>("loading");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ios, setIos] = useState(false);

  const refresh = useCallback(async () => {
    setIos(isIos());
    if (!pushSupported()) {
      setStatus(isIos() && !isStandalone() ? "ios-install" : "unsupported");
      return;
    }
    if (Notification.permission === "denied") {
      setStatus("denied");
      return;
    }
    try {
      await fetchVapidKey();
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setStatus("server-off");
        return;
      }
      // Diğer hatalar: düğmeyi yine göster, açma denemesinde hata mesajı çıkar.
    }
    const reg = await navigator.serviceWorker.getRegistration("/");
    const sub = await reg?.pushManager.getSubscription();
    setStatus(sub ? "on" : "off");
  }, []);

  useEffect(() => {
    refresh().catch(() => setStatus("off"));
  }, [refresh]);

  async function enable() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setStatus(permission === "denied" ? "denied" : "off");
        if (permission !== "denied") setError("Bildirim izni verilmedi.");
        return;
      }
      let key: string;
      try {
        key = await fetchVapidKey();
      } catch (err) {
        if (err instanceof ApiError && err.status === 503) {
          setStatus("server-off");
          return;
        }
        throw err;
      }
      const reg = await getRegistration();
      const old = await reg.pushManager.getSubscription();
      const sub =
        old ??
        (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) }));
      const { endpoint, keys } = sub.toJSON();
      try {
        await request("/push/subscribe", {
          method: "POST",
          body: JSON.stringify({ endpoint, keys: { p256dh: keys?.p256dh, auth: keys?.auth } }),
        });
      } catch (err) {
        await sub.unsubscribe().catch(() => {});
        if (err instanceof ApiError && err.status === 503) {
          setStatus("server-off");
          return;
        }
        throw err;
      }
      setStatus("on");
      setMessage("Hatırlatmalar açıldı. İlaç saatinizde bildirim alacaksınız.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const reg = await navigator.serviceWorker.getRegistration("/");
      const sub = await reg?.pushManager.getSubscription();
      if (sub) {
        await request("/push/subscribe", {
          method: "DELETE",
          body: JSON.stringify({ endpoint: sub.endpoint }),
        }).catch((err) => {
          // Sunucuda kayıt zaten yoksa yerel aboneliği yine de kaldır.
          if (!(err instanceof ApiError && err.status === 404)) throw err;
        });
        await sub.unsubscribe();
      }
      setStatus("off");
      setMessage("Hatırlatmalar kapatıldı.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const { sent } = await request<{ sent: number }>("/push/test", { method: "POST" });
      if (sent > 0) setMessage("Test bildirimi gönderildi.");
      else setError("Test bildirimi gönderilemedi. Hatırlatmaları kapatıp yeniden açmayı deneyin.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setStatus("server-off");
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const on = status === "on";

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {on ? <BellRing className="size-4 text-primary" /> : <Bell className="size-4" />}
          {text.title}
        </CardTitle>
        <CardDescription>{text.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {status === "loading" && <p className="text-muted-foreground">Kontrol ediliyor…</p>}

        {status === "ios-install" && (
          <Notice icon={<Smartphone className="size-4" />}>
            iOS&apos;ta yalnızca ana ekrana eklenmiş uygulamada çalışır (iOS 16.4+). Safari&apos;de Paylaş →
            &quot;Ana Ekrana Ekle&quot; adımlarını izleyip uygulamayı ana ekrandan açın.
          </Notice>
        )}

        {status === "unsupported" && (
          <Notice icon={<TriangleAlert className="size-4" />}>
            Bu tarayıcı bildirimleri desteklemiyor. Güncel Chrome, Edge, Firefox veya Safari kullanın.
            {ios ? " iOS'ta yalnızca ana ekrana eklenmiş uygulamada çalışır, iOS 16.4+." : ""}
          </Notice>
        )}

        {status === "denied" && (
          <Notice icon={<BellOff className="size-4" />}>
            Bildirim izni reddedilmiş. Tarayıcı ayarlarından bu site için bildirimlere izin verip sayfayı yenileyin.
          </Notice>
        )}

        {status === "server-off" && (
          <Notice icon={<TriangleAlert className="size-4" />}>
            Bildirim servisi sunucuda henüz yapılandırılmamış. Lütfen daha sonra tekrar deneyin.
          </Notice>
        )}

        {(status === "off" || status === "on") && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={on ? "outline" : "default"}
              size="lg"
              disabled={busy}
              aria-pressed={on}
              onClick={on ? disable : enable}
            >
              {on ? <BellOff /> : <Bell />}
              {on ? text.off : text.on}
            </Button>
            {on && (
              <Button variant="ghost" size="lg" disabled={busy} onClick={sendTest}>
                Test bildirimi gönder
              </Button>
            )}
          </div>
        )}

        {status === "off" && ios && (
          <p className="text-xs text-muted-foreground">
            iOS&apos;ta yalnızca ana ekrana eklenmiş uygulamada çalışır, iOS 16.4+.
          </p>
        )}

        <div aria-live="polite" className="text-sm">
          {message && <p className="text-primary">{message}</p>}
          {error && <p className="text-destructive">{error}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function Notice({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-muted p-3 text-sm text-muted-foreground">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <p>{children}</p>
    </div>
  );
}
