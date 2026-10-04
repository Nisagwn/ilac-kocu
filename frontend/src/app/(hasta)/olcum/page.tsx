"use client";

import { PatientGate } from "@/components/patient/states";
import { MeasurementScreen } from "@/components/patient/measurement-screen";

export default function Page() {
  return <PatientGate>{(patientId) => <MeasurementScreen patientId={patientId} />}</PatientGate>;
}
