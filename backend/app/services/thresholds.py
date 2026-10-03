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
