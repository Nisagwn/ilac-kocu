"""Ölçüm eşikleri. Değerler genel kabul gören klinik eşiklerdir; danışman hekimce gözden geçirilmelidir."""

from dataclasses import dataclass

from app.models import MeasurementType, Severity


@dataclass(frozen=True)
class ThresholdHit:
    severity: Severity
    message: str


def evaluate_measurement(mtype: MeasurementType, value: float, value2: float | None = None) -> ThresholdHit | None:
    if mtype in (MeasurementType.glucose_fasting, MeasurementType.glucose_postprandial):
        if value < 54:
            return ThresholdHit(Severity.critical, f"Ciddi düşük kan şekeri: {value:.0f} mg/dL")
        if value < 70:
            return ThresholdHit(Severity.warning, f"Düşük kan şekeri: {value:.0f} mg/dL")
        if value > 300:
            return ThresholdHit(Severity.critical, f"Çok yüksek kan şekeri: {value:.0f} mg/dL")
        if mtype == MeasurementType.glucose_fasting and value > 180:
            return ThresholdHit(Severity.warning, f"Yüksek açlık kan şekeri: {value:.0f} mg/dL")
        return None

    if mtype == MeasurementType.blood_pressure:
        dia = value2 or 0
        if value >= 180 or dia >= 120:
            return ThresholdHit(Severity.critical, f"Çok yüksek tansiyon: {value:.0f}/{dia:.0f} mmHg")
        if value < 90 or (value2 is not None and dia < 60):
            return ThresholdHit(Severity.warning, f"Düşük tansiyon: {value:.0f}/{dia:.0f} mmHg")
        if value >= 160 or dia >= 100:
            return ThresholdHit(Severity.warning, f"Yüksek tansiyon: {value:.0f}/{dia:.0f} mmHg")
    return None


# --- Hedef aralıklar (hekim dashboard'u "hedefte kalma" oranları) -------------------------------
# Uyarı eşiklerinden (yukarıda) farklıdır: uyarı eşikleri klinik dikkat gerektiren uçları, hedefler ise
# bireysel hedef konulmamış erişkin Tip 2 diyabet / hipertansiyon hastası için genel kabul gören tedavi
# hedeflerini gösterir. Tek doğruluk kaynağı burasıdır (kullanan: services/clinical_insights.py).
# Kaynaklar:
#   - Kan şekeri: ADA Standards of Care in Diabetes 2024, Bölüm 6 "Glycemic Goals and Hypoglycemia" —
#     öğün öncesi (açlık) kapiller plazma glukozu 80–130 mg/dL, öğün sonrası tepe değer <180 mg/dL.
#   - Tansiyon: ADA Standards of Care 2024 Bölüm 10 ve 2017 ACC/AHA kılavuzu — <130/80 mmHg.
# Hekim bireysel hedef belirleyebilir; değerler danışman hekimce gözden geçirilmelidir.
GLUCOSE_FASTING_TARGET_MIN = 80.0  # mg/dL, dahil
GLUCOSE_FASTING_TARGET_MAX = 130.0  # mg/dL, dahil
GLUCOSE_POSTPRANDIAL_TARGET_MAX = 180.0  # mg/dL, hariç (<180)
BP_SYSTOLIC_TARGET_MAX = 130.0  # mmHg, hariç (<130)
BP_DIASTOLIC_TARGET_MAX = 80.0  # mmHg, hariç (<80)

TARGET_LABELS: dict[MeasurementType, str] = {
    MeasurementType.glucose_fasting: "80–130 mg/dL",
    MeasurementType.glucose_postprandial: "<180 mg/dL",
    MeasurementType.blood_pressure: "<130/80 mmHg",
}


def is_in_target(mtype: MeasurementType, value: float, value2: float | None = None) -> bool:
    """Ölçüm genel tedavi hedefi içinde mi? Diyastoliği olmayan tansiyon ölçümü hedefte sayılmaz."""
    if mtype == MeasurementType.glucose_fasting:
        return GLUCOSE_FASTING_TARGET_MIN <= value <= GLUCOSE_FASTING_TARGET_MAX
    if mtype == MeasurementType.glucose_postprandial:
        return value < GLUCOSE_POSTPRANDIAL_TARGET_MAX
    if mtype == MeasurementType.blood_pressure:
        return value2 is not None and value < BP_SYSTOLIC_TARGET_MAX and value2 < BP_DIASTOLIC_TARGET_MAX
    return False
