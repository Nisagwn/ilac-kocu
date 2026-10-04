import { NextResponse } from "next/server";
import { API_URL, ROLE_COOKIE, TOKEN_COOKIE } from "@/lib/server";

export async function POST(req: Request) {
  const { email, password } = await req.json();
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email ?? "", password: password ?? "" }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(body, { status: res.status });

  const out = NextResponse.json({ role: body.role });
  const opts = {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60,
  };
  out.cookies.set(TOKEN_COOKIE, body.access_token, opts);
  // Rol çerezi yalnızca yönlendirme içindir; yetki kararı her zaman backend'de JWT ile verilir.
  out.cookies.set(ROLE_COOKIE, body.role, opts);
  return out;
}
