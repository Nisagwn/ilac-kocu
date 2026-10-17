import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  API_BASE,
  clearAuthCookies,
  clientIpFrom,
  REFRESH_COOKIE,
  refreshTokens,
  setAuthCookies,
  TOKEN_COOKIE,
  type TokenPair,
} from "@/lib/server";

// Backend'e iletilen istemci başlıkları ve tarayıcıya geri verilen yanıt başlıkları (ETag/304, istek sınırı,
// sayfalama, idempotency). Diğer başlıklar (çerezler dahil) iletilmez.
const FORWARD_REQUEST = ["idempotency-key", "if-none-match", "x-request-id"];
const FORWARD_RESPONSE = [
  "etag",
  "cache-control",
  "retry-after",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
  "x-ratelimit-reset",
  "x-next-cursor",
  "idempotent-replayed",
  "x-request-id",
];

// Tarayıcı → /api/proxy/<yol> → FastAPI /v1. Token httpOnly çerezden Authorization başlığına eklenir.
// Backend 401 dönerse ve refresh çerezi varsa oturum bir kez yenilenir, yeni çerezler yanıta yazılır
// ve özgün istek bir kez tekrarlanır.
async function forward(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  const url = new URL(req.url);
  const target = `${API_BASE}/${path.map(encodeURIComponent).join("/")}${url.search}`;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? await req.text() : undefined;
  const ip = clientIpFrom(req);

  const send = (accessToken: string | undefined) => {
    const headers: Record<string, string> = {
      "Content-Type": req.headers.get("content-type") ?? "application/json",
    };
    for (const name of FORWARD_REQUEST) {
      const v = req.headers.get(name);
      if (v) headers[name] = v;
    }
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
    res.status === 204 || res.status === 304
      ? new NextResponse(null, { status: res.status })
      : new NextResponse(await res.text(), {
          status: res.status,
          headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
        });
  for (const name of FORWARD_RESPONSE) {
    const v = res.headers.get(name);
    if (v) out.headers.set(name, v);
  }

  if (renewed) setAuthCookies(out, renewed);
  // Yenilenemeyen oturum: çerezler temizlenir ki middleware kullanıcıyı /giris'e yönlendirebilsin
  else if (res.status === 401 && (token || refresh)) clearAuthCookies(out);
  return out;
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
