import { NextResponse } from "next/server";
import { API_BASE, clientIpFrom, setAuthCookies, type TokenPair } from "@/lib/server";

export async function POST(req: Request) {
  const { email, password } = await req.json();
  const headers: Record<string, string> = { "Content-Type": "application/x-www-form-urlencoded" };
  // Backend IP başına giriş sınırı uygular; gerçek istemci IP'si iletilir (güven varsayımı: lib/server.ts)
  const ip = clientIpFrom(req);
  if (ip) headers["X-Forwarded-For"] = ip;

  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers,
    body: new URLSearchParams({ username: email ?? "", password: password ?? "" }),
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const out = NextResponse.json(body, { status: res.status });
    const retryAfter = res.headers.get("retry-after");
    if (retryAfter) out.headers.set("Retry-After", retryAfter);
    return out;
  }

  const pair = body as TokenPair;
  const out = NextResponse.json({ role: pair.role });
  setAuthCookies(out, pair);
  return out;
}
