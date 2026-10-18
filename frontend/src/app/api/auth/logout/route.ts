import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { API_URL, clearAuthCookies, REFRESH_COOKIE, refreshTokens, TOKEN_COOKIE } from "@/lib/server";

async function backendLogout(access: string, refresh: string | undefined) {
  return fetch(`${API_URL}/v1/auth/logout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${access}`, "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh ?? null }),
    cache: "no-store",
  }).catch(() => null);
}

// Çıkış: backend erişim token'ını iptal listesine ekler ve refresh token'ı iptal eder; ardından çerezler silinir.
export async function POST() {
  const jar = await cookies();
  const access = jar.get(TOKEN_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;

  const res = access ? await backendLogout(access, refresh) : null;
  if ((!res || res.status === 401) && refresh) {
    // Erişim token'ı yok/süresi dolmuş: önce yenile, sonra yeni çifti iptal et (zincir tamamen kapanır)
    const pair = await refreshTokens(refresh);
    if (pair) await backendLogout(pair.access_token, pair.refresh_token);
  }

  const out = NextResponse.json({ ok: true });
  clearAuthCookies(out);
  return out;
}
