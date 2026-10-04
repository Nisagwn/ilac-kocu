import type { Metadata } from "next";
import { AlertsBoard } from "@/components/clinician/alerts-board";

export const metadata: Metadata = { title: "Uyarılar · Hekim Paneli" };

export default function ClinicianAlertsPage() {
  return <AlertsBoard />;
}
