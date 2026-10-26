import { NextResponse, type NextRequest } from "next/server";
import { REFRESH_COOKIE, ROLE_COOKIE, TOKEN_COOKIE } from "@/lib/server";

// Rol → ana sayfa. Bu yalnızca kullanıcı deneyimi içindir; veri erişimi backend'de denetlenir.
export const HOME: Record<string, string> = { patient: "/bugun", clinician: "/hekim", caregiver: "/yakin" };

const PATIENT_ONLY = ["/bugun", "/olcum", "/koc", "/ilaclar", "/ozet"];

/** İstek başına 128 bit kriptografik rastgele nonce (Web Crypto; Edge runtime uyumlu), base64. */
function createNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/**
 * Sayfa CSP'si (tek kaynak burası; nginx CSP eklemez).
 * - script-src: yalnız bu isteğin nonce'u + 'strict-dynamic' (nonce'lu Next.js betiklerinin yüklediği
 *   parçalar da güvenilir). 'unsafe-inline' YOK. 'unsafe-eval' yalnız `next dev` (React hata ayıklama
 *   araçları eval kullanır); üretim derlemesinde yoktur.
 * - style-src 'unsafe-inline': Recharts SVG'ye ve bileşenler `style=""` özniteliği yazar; öznitelik
 *   stillerine nonce verilemez. Stil enjeksiyonu betik çalıştıramaz; risk betik tarafına göre düşüktür.
 *   (style-src'ye nonce EKLENMEMELİ: nonce varken tarayıcı 'unsafe-inline'ı yok sayar.)
 * - connect-src 'self': tarayıcı yalnız kendi kökenine (/api/proxy) istek atar; web push aboneliği
 *   tarayıcının push servisine tarayıcı tarafından gider, connect-src'yi etkilemez.
 */
function buildCsp(nonce: string): string {
  const dev = process.env.NODE_ENV === "development";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** Rol yönlendirme kararı: null → sayfaya devam, aksi halde yönlendirilecek yol. */
function redirectTarget(req: NextRequest): string | null {
  const { pathname } = req.nextUrl;
  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  const role = req.cookies.get(ROLE_COOKIE)?.value ?? "";
  // Erişim çerezi kısa ömürlüdür (15 dk) ve düşebilir; oturum yenileme token'ıyla sürer. Refresh çerezi
  // yalnızca /api yoluna gönderildiğinden burada genellikle görünmez; bu yüzden onunla aynı ömürlü rol
  // çerezi de "oturum var" ipucu sayılır. Oturum gerçekten bittiyse proxy ilk 401'de çerezleri temizler.
  const loggedIn = Boolean(token || req.cookies.get(REFRESH_COOKIE)?.value || role);

  if (!loggedIn) return pathname === "/giris" ? null : "/giris";
  const home = HOME[role] ?? "/giris";
  if (home === "/giris") return pathname === "/giris" ? null : "/giris";
  if (pathname === "/" || pathname === "/giris") return home;
  const wrongArea =
    (PATIENT_ONLY.some((p) => pathname.startsWith(p)) && role !== "patient") ||
    (pathname.startsWith("/hekim") && role !== "clinician") ||
    (pathname.startsWith("/yakin") && role !== "caregiver");
  return wrongArea ? home : null;
}

export function middleware(req: NextRequest) {
  const target = redirectTarget(req);
  if (target) return NextResponse.redirect(new URL(target, req.url));

  const nonce = createNonce();
  const csp = buildCsp(nonce);
  // Next.js nonce'u istek başlığındaki CSP'den okur ve kendi <script> etiketlerine ekler;
  // x-nonce ise uygulama kodunun (headers()) özel betiklere vermesi içindir.
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const res = NextResponse.next({ request: { headers: requestHeaders } });
  res.headers.set("Content-Security-Policy", csp);
  return res;
}

export const config = {
  // Sayfa rotaları kapsanır; API, Next derleme çıktısı (_next/static, _next/image, HMR), favicon,
  // manifest, service worker, çevrimdışı sayfa ve ikonlar hariç. Rol yönlendirmesi RSC/prefetch
  // isteklerinde de çalışmalı; bu yüzden prefetch istekleri hariç tutulmaz.
  matcher: ["/((?!api|_next|favicon.ico|manifest.webmanifest|sw.js|offline.html|icons).*)"],
};
