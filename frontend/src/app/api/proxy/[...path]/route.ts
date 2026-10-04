import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  API_URL,
  clearAuthCookies,
  clientIpFrom,
  REFRESH_COOKIE,
  refreshTokens,
  setAuthCookies,
  TOKEN_COOKIE,
  type TokenPair,
} from "@/lib/server";

// Tarayıcı → /api/proxy/<yol> → FastAPI. Token httpOnly çerezden Authorization başlığına eklenir.
// Backend 401 dönerse ve refresh çerezi varsa oturum bir kez yenilenir, yeni çerezler yanıta yazılır
// ve özgün istek bir kez tekrarlanır.
async function forward(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  const url = new URL(req.url);
  const target = `${API_URL}/${path.map(encodeURIComponent).join("/")}${url.search}`;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? await req.text() : undefined;
  const ip = clientIpFrom(req);

  const send = (accessToken: string | undefined) => {
    const headers: Record<string, string> = {
      "Content-Type": req.headers.get("content-type") ?? "application/json",
    };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    if (ip) headers["X-Forwarded-For"] = ip; // ör. kayıt için IP başına istek sınırı
    return fetch(target, { method: req.method, headers, body, cache: "no-store" });
  };

  let res = await send(token);
  let renewed: TokenPair | null = null;
  if (res.status === 401 && refresh) {
    renewed = await refreshTokens(refresh);
    if (renewed) res = await send(renewed.access_token);
  }

  const out =
    res.status === 204
      ? new NextResponse(null, { status: 204 })
      : new NextResponse(await res.text(), {
          status: res.status,
          headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
        });
  const retryAfter = res.headers.get("retry-after");
  if (retryAfter) out.headers.set("Retry-After", retryAfter);

  if (renewed) setAuthCookies(out, renewed);
  // Yenilenemeyen oturum: çerezler temizlenir ki middleware kullanıcıyı /giris'e yönlendirebilsin
  else if (res.status === 401 && (token || refresh)) clearAuthCookies(out);
  return out;
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
