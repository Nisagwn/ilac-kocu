import type { Metadata } from "next";
import { CaregiverPatientList } from "@/components/clinician/caregiver-patient-list";

export const metadata: Metadata = { title: "Hastalarım · Yakın Paneli" };

export default function CaregiverHomePage() {
  return <CaregiverPatientList />;
}
