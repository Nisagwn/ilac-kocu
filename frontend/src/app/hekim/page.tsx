import type { Metadata } from "next";
import { ClinicianPatientList } from "@/components/clinician/patient-list";

export const metadata: Metadata = { title: "Hastalar · Hekim Paneli" };

export default function ClinicianHomePage() {
  return <ClinicianPatientList />;
}
