"use client";

import { PatientGate } from "@/components/patient/states";
import { MedicationsScreen } from "@/components/patient/medications-screen";

export default function Page() {
  return <PatientGate>{(patientId) => <MedicationsScreen patientId={patientId} />}</PatientGate>;
}
