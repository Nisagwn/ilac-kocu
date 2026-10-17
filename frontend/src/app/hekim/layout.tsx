"use client";

import { Bell, BookOpenCheck, Users } from "lucide-react";
import { AppShell, type NavItem } from "@/components/app-shell";

const NAV: NavItem[] = [
  { href: "/hekim", label: "Hastalar", icon: Users },
  { href: "/hekim/uyarilar", label: "Uyarılar", icon: Bell },
  { href: "/hekim/egitim", label: "Eğitim içeriği", icon: BookOpenCheck },
];

export default function ClinicianLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell title="Hekim Paneli" nav={NAV}>
      {children}
    </AppShell>
  );
}
