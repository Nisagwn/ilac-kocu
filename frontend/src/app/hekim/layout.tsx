"use client";

import { Bell, Users } from "lucide-react";
import { AppShell, type NavItem } from "@/components/app-shell";

const NAV: NavItem[] = [
  { href: "/hekim", label: "Hastalar", icon: Users },
  { href: "/hekim/uyarilar", label: "Uyarılar", icon: Bell },
];

export default function ClinicianLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell title="Hekim Paneli" nav={NAV}>
      {children}
    </AppShell>
  );
}
