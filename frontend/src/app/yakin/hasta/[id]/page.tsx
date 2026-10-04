import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PatientDetail } from "@/components/clinician/patient-detail";

export const metadata: Metadata = { title: "Hasta detayı · Yakın Paneli" };

export default async function CaregiverPatientPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const patientId = Number(id);
  if (!Number.isInteger(patientId) || patientId <= 0) notFound();
  return <PatientDetail patientId={patientId} readOnly />;
}
