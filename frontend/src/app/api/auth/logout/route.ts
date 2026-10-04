import { NextResponse } from "next/server";
import { ROLE_COOKIE, TOKEN_COOKIE } from "@/lib/server";

export async function POST() {
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(TOKEN_COOKIE);
  out.cookies.delete(ROLE_COOKIE);
  return out;
}
