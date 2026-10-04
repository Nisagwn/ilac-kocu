"use client";

import { PatientGate } from "@/components/patient/states";
import { TodayScreen } from "@/components/patient/today-screen";

export default function Page() {
  return <PatientGate>{(patientId) => <TodayScreen patientId={patientId} />}</PatientGate>;
}
