import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { clearAuthCookies, REFRESH_COOKIE, refreshTokens, setAuthCookies } from "@/lib/server";

// Oturumu yeniler: refresh çerezi backend'de döndürülür, yeni çift httpOnly çerezlere yazılır.
export async function POST() {
  const refresh = (await cookies()).get(REFRESH_COOKIE)?.value;
  const pair = refresh ? await refreshTokens(refresh) : null;
  if (!pair) {
    const out = NextResponse.json({ detail: "Oturum yenilenemedi" }, { status: 401 });
    clearAuthCookies(out);
    return out;
  }
  const out = NextResponse.json({ role: pair.role });
  setAuthCookies(out, pair);
  return out;
}
