"use client";

import { PatientGate } from "@/components/patient/states";
import { SummaryScreen } from "@/components/patient/summary-screen";

export default function Page() {
  return <PatientGate>{(patientId) => <SummaryScreen patientId={patientId} />}</PatientGate>;
}
