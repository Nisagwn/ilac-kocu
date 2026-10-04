"use client";

import { Activity, CalendarCheck, FileBarChart, MessageCircle, Pill } from "lucide-react";
import { AppShell, type NavItem } from "@/components/app-shell";

const NAV: NavItem[] = [
  { href: "/bugun", label: "Bugün", icon: CalendarCheck },
  { href: "/olcum", label: "Ölçüm", icon: Activity },
  { href: "/koc", label: "Koç", icon: MessageCircle },
  { href: "/ilaclar", label: "İlaçlar", icon: Pill },
  { href: "/ozet", label: "Özet", icon: FileBarChart },
];

export default function PatientLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell title="İlaç Koçu" nav={NAV}>
      {children}
    </AppShell>
  );
}
