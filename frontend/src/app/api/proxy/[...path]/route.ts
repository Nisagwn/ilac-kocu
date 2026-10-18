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

// İstemciden backend'e iletilen başlıklar (idempotent yeniden deneme, ETag ile koşullu GET)
const FORWARD_REQUEST_HEADERS = ["Idempotency-Key", "If-None-Match"];
// Backend'den tarayıcıya iletilen başlıklar (istek sınırı, önbellek doğrulama, sayfalama, idempotency)
const FORWARD_RESPONSE_HEADERS = [
  "Retry-After",
  "X-RateLimit-Limit",
  "X-RateLimit-Remaining",
  "ETag",
  "Cache-Control",
  "Vary",
  "X-Next-Cursor",
  "Idempotent-Replayed",
  "Deprecation",
];

// Tarayıcı → /api/proxy/<yol> → FastAPI /v1/<yol>. Token httpOnly çerezden Authorization başlığına eklenir.
// Backend 401 dönerse ve refresh çerezi varsa oturum bir kez yenilenir, yeni çerezler yanıta yazılır
// ve özgün istek bir kez tekrarlanır (Idempotency-Key aynı kalır).
async function forward(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  const url = new URL(req.url);
  // Backend iş uçları /v1 altında; istemci sürümü vermediyse eklenir
  const segments = path[0] === "v1" ? path : ["v1", ...path];
  const target = `${API_URL}/${segments.map(encodeURIComponent).join("/")}${url.search}`;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? await req.text() : undefined;
  const ip = clientIpFrom(req);

  const send = (accessToken: string | undefined) => {
    const headers: Record<string, string> = {
      "Content-Type": req.headers.get("content-type") ?? "application/json",
    };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    if (ip) headers["X-Forwarded-For"] = ip; // ör. kayıt için IP başına istek sınırı
    for (const name of FORWARD_REQUEST_HEADERS) {
      const value = req.headers.get(name);
      if (value) headers[name] = value;
    }
    return fetch(target, { method: req.method, headers, body, cache: "no-store" });
  };

  let res = await send(token);
  let renewed: TokenPair | null = null;
  if (res.status === 401 && refresh) {
    renewed = await refreshTokens(refresh);
    if (renewed) res = await send(renewed.access_token);
  }

  const out =
    res.status === 204 || res.status === 304
      ? new NextResponse(null, { status: res.status })
      : new NextResponse(await res.text(), {
          status: res.status,
          headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
        });
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = res.headers.get(name);
    if (value) out.headers.set(name, value);
  }

  if (renewed) setAuthCookies(out, renewed);
  // Yenilenemeyen oturum: çerezler temizlenir ki middleware kullanıcıyı /giris'e yönlendirebilsin
  else if (res.status === 401 && (token || refresh)) clearAuthCookies(out);
  return out;
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
