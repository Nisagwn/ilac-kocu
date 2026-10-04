"use client";

import { Users } from "lucide-react";
import { AppShell, type NavItem } from "@/components/app-shell";

const NAV: NavItem[] = [{ href: "/yakin", label: "Hastalarım", icon: Users }];

export default function CaregiverLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell title="Yakın Paneli" nav={NAV}>
      {children}
    </AppShell>
  );
}
