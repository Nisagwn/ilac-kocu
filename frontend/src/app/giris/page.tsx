"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const HOME: Record<string, string> = { patient: "/bugun", clinician: "/hekim", caregiver: "/yakin" };
const DEMO = [
  ["Hasta", "hasta@demo.local"],
  ["Hekim", "hekim@demo.local"],
  ["Yakın", "yakin@demo.local"],
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const body = await res.json().catch(() => ({}));
    setLoading(false);
    if (!res.ok) return setError(typeof body.detail === "string" ? body.detail : "Giriş başarısız");
    router.replace(HOME[body.role] ?? "/");
    router.refresh();
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-teal-700">İlaç Koçu</CardTitle>
          <CardDescription>İlaç uyumu ve yaşam tarzı koçu</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">E-posta</Label>
              <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Şifre</Label>
              <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Giriş yapılıyor…" : "Giriş yap"}
            </Button>
          </form>
          <div className="mt-6 space-y-2 border-t pt-4">
            <p className="text-xs text-muted-foreground">Demo hesaplar (şifre: demo12345)</p>
            <div className="flex gap-2">
              {DEMO.map(([label, mail]) => (
                <Button key={mail} variant="outline" size="sm" onClick={() => { setEmail(mail); setPassword("demo12345"); }}>
                  {label}
                </Button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
