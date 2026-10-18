import type { Metadata } from "next";
import { EducationReview } from "@/components/clinician/education-review";

export const metadata: Metadata = { title: "Eğitim içeriği · Hekim Paneli" };

export default function ClinicianEducationPage() {
  return <EducationReview />;
}
