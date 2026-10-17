// Yalnızca sunucu tarafı (route handler / middleware) yardımcıları.
import type { NextResponse } from "next/server";

export const API_URL = process.env.API_URL ?? "http://localhost:8000";
/** Sürümlü API kökü: tüm backend çağrıları buradan (sürümsüz eski yollar yalnızca geriye dönük). */
export const API_BASE = `${API_URL}/v1`;
export const TOKEN_COOKIE = "ik_token";
export const REFRESH_COOKIE = "ik_refresh";
export const ROLE_COOKIE = "ik_role";

// Yenileme çerezinin yolu "/api": tarayıcı onu yalnızca /api/auth/* ve /api/proxy/* route handler'larına
// gönderir; sayfa, statik dosya ve asset isteklerine hiç eklenmez. "/api/auth" ile sınırlanmaz çünkü
// proxy, 401 aldığında oturumu sunucu tarafında şeffafça yenileyebilmek için bu çereze ihtiyaç duyar.
// Middleware bu çerezi göremez; "oturum var" ipucu olarak aynı ömürlü rol çerezini kullanır.
export const REFRESH_PATH = "/api";
const REFRESH_MAX_AGE = 60 * 60 * 24 * 14; // 14 gün (backend refresh_token_days ile aynı)

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  role: string;
  expires_in: number;
  refresh_expires_in?: number;
};

const secure = process.env.NODE_ENV === "production";

/** Token çiftini httpOnly çerezlere yazar. Rol çerezi yalnızca yönlendirme içindir. */
export function setAuthCookies(res: NextResponse, pair: TokenPair) {
  const accessMaxAge = pair.expires_in > 0 ? pair.expires_in : 15 * 60;
  const refreshMaxAge = pair.refresh_expires_in ?? REFRESH_MAX_AGE;
  res.cookies.set(TOKEN_COOKIE, pair.access_token, {
    httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: accessMaxAge,
  });
  res.cookies.set(REFRESH_COOKIE, pair.refresh_token, {
    httpOnly: true, sameSite: "strict", secure, path: REFRESH_PATH, maxAge: refreshMaxAge,
  });
  // Rol çerezi yenileme token'ı kadar yaşar: erişim çerezi düşse de yönlendirme doğru kalır.
  // Yetki kararı her zaman backend'de JWT ile verilir.
  res.cookies.set(ROLE_COOKIE, pair.role, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: refreshMaxAge });
}

export function clearAuthCookies(res: NextResponse) {
  res.cookies.set(TOKEN_COOKIE, "", { path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { path: REFRESH_PATH, maxAge: 0 });
  res.cookies.set(ROLE_COOKIE, "", { path: "/", maxAge: 0 });
}

/**
 * Backend'e iletilecek istemci IP'si. Gelen X-Forwarded-For'un EN SAĞDAKİ değeri (önümüzdeki en yakın
 * proxy'nin eklediği) ya da X-Real-IP kullanılır. Güven varsayımı: production'da Next.js'in önünde
 * bu başlığı istemciden gelen değerin üzerine yazan bir ters proxy (ör. nginx `$remote_addr`) bulunur.
 * Backend bu başlığa yalnızca TRUSTED_PROXIES içindeki adreslerden (bu Next sunucusu) güvenir.
 */
export function clientIpFrom(req: Request): string | null {
  const xff = req.headers.get("x-forwarded-for");
  const last = xff?.split(",").pop()?.trim();
  return last || req.headers.get("x-real-ip")?.trim() || null;
}

// Aynı yenileme token'ı ile eşzamanlı gelen istekler backend'e tek yenileme isteği gönderir. Aksi halde
// ikinci istek "döndürülmüş token tekrar kullanıldı" sayılır ve tüm oturumlar iptal edilir.
// Sonuç kısa süre saklanır; eski çerezle biraz geç gelen istekler de aynı yeni çifti alır.
// (Tek Next.js süreci varsayımı; birden çok örnekte yapışkan oturum gerekir.)
const inflight = new Map<string, { promise: Promise<TokenPair | null>; at: number }>();
const REUSE_MS = 30_000;

export function refreshTokens(refreshToken: string): Promise<TokenPair | null> {
  const now = Date.now();
  for (const [k, v] of inflight) if (now - v.at > REUSE_MS) inflight.delete(k);
  const hit = inflight.get(refreshToken);
  if (hit) return hit.promise;
  const promise = fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  })
    .then(async (r) => (r.ok ? ((await r.json()) as TokenPair) : null))
    .catch(() => null);
  inflight.set(refreshToken, { promise, at: now });
  return promise;
}
