"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { LucideIcon } from "lucide-react";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

/**
 * Ortak sayfa çerçevesi. Mobilde altta sekme çubuğu, geniş ekranda üstte menü.
 * Her rol kendi layout.tsx'inde kendi nav listesini verir.
 */
/** En uzun eşleşen nav öğesi aktiftir (/hekim/uyarilar'da yalnızca "Uyarılar" yanar). */
function activeHref(nav: NavItem[], pathname: string): string | undefined {
  return nav
    .filter((n) => pathname === n.href || pathname.startsWith(`${n.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href;
}

export function AppShell({ nav, title, children }: { nav: NavItem[]; title: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const active = activeHref(nav, pathname);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/giris");
  }

  return (
    <div className="min-h-dvh bg-muted/30 pb-20 md:pb-0">
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <span className="font-semibold text-teal-700">{title}</span>
          <nav className="hidden flex-1 gap-1 md:flex">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted",
                  active === n.href && "bg-muted font-medium text-foreground",
                )}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={logout}>
            <LogOut /> Çıkış
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      <nav className="fixed inset-x-0 bottom-0 z-10 grid border-t bg-background md:hidden" style={{ gridTemplateColumns: `repeat(${nav.length}, 1fr)` }}>
        {nav.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            className={cn(
              "flex flex-col items-center gap-0.5 py-2 text-xs text-muted-foreground",
              active === n.href && "text-teal-700",
            )}
          >
            <n.icon className="size-5" />
            {n.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

export function EmergencyBanner() {
  return (
    <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
      Koç teşhis koymaz ve ilaç dozu önermez. Acil durumda <strong>112</strong>&apos;yi arayın.
    </p>
  );
}
