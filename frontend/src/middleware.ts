import { NextResponse, type NextRequest } from "next/server";
import { REFRESH_COOKIE, ROLE_COOKIE, TOKEN_COOKIE } from "@/lib/server";

// Rol → ana sayfa. Bu yalnızca kullanıcı deneyimi içindir; veri erişimi backend'de denetlenir.
export const HOME: Record<string, string> = { patient: "/bugun", clinician: "/hekim", caregiver: "/yakin" };

const PATIENT_ONLY = ["/bugun", "/olcum", "/koc", "/ilaclar", "/ozet"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  const role = req.cookies.get(ROLE_COOKIE)?.value ?? "";
  // Erişim çerezi kısa ömürlüdür (15 dk) ve düşebilir; oturum yenileme token'ıyla sürer. Refresh çerezi
  // yalnızca /api yoluna gönderildiğinden burada genellikle görünmez; bu yüzden onunla aynı ömürlü rol
  // çerezi de "oturum var" ipucu sayılır. Oturum gerçekten bittiyse proxy ilk 401'de çerezleri temizler.
  const loggedIn = Boolean(token || req.cookies.get(REFRESH_COOKIE)?.value || role);

  if (!loggedIn) {
    if (pathname === "/giris") return NextResponse.next();
    return NextResponse.redirect(new URL("/giris", req.url));
  }
  const home = HOME[role] ?? "/giris";
  if (home === "/giris") return pathname === "/giris" ? NextResponse.next() : NextResponse.redirect(new URL("/giris", req.url));
  if (pathname === "/" || pathname === "/giris") return NextResponse.redirect(new URL(home, req.url));
  const wrongArea =
    (PATIENT_ONLY.some((p) => pathname.startsWith(p)) && role !== "patient") ||
    (pathname.startsWith("/hekim") && role !== "clinician") ||
    (pathname.startsWith("/yakin") && role !== "caregiver");
  if (wrongArea) return NextResponse.redirect(new URL(home, req.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next|favicon.ico|manifest.webmanifest|sw.js|offline.html|icons).*)"],
};
