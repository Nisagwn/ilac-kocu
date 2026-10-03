from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import DB, CurrentUser, get_patient_for_read, get_patient_for_write
from app.models import Medication, MedicationSchedule
from app.schemas import MedicationIn, MedicationOut, MedicationUpdate

router = APIRouter(prefix="/patients/{patient_id}/medications", tags=["medications"])


def _get_med(db, patient_id: int, med_id: int) -> Medication:
    med = db.get(Medication, med_id)
    if med is None or med.patient_id != patient_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "İlaç bulunamadı")
    return med


@router.get("", response_model=list[MedicationOut])
def list_medications(patient_id: int, db: DB, user: CurrentUser, include_inactive: bool = False):
    get_patient_for_read(db, user, patient_id)
    q = select(Medication).where(Medication.patient_id == patient_id)
    if not include_inactive:
        q = q.where(Medication.active.is_(True))
    return db.scalars(q.order_by(Medication.name)).all()


@router.post("", response_model=MedicationOut, status_code=status.HTTP_201_CREATED)
def create_medication(patient_id: int, data: MedicationIn, db: DB, user: CurrentUser):
    get_patient_for_write(db, user, patient_id)
    med = Medication(patient_id=patient_id, **data.model_dump(exclude={"schedules"}))
    med.schedules = [MedicationSchedule(**s.model_dump()) for s in data.schedules]
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


@router.get("/{med_id}", response_model=MedicationOut)
def get_medication(patient_id: int, med_id: int, db: DB, user: CurrentUser):
    get_patient_for_read(db, user, patient_id)
    return _get_med(db, patient_id, med_id)


@router.patch("/{med_id}", response_model=MedicationOut)
def update_medication(patient_id: int, med_id: int, data: MedicationUpdate, db: DB, user: CurrentUser):
    get_patient_for_write(db, user, patient_id)
    med = _get_med(db, patient_id, med_id)
    payload = data.model_dump(exclude_unset=True, exclude={"schedules"})
    for k, v in payload.items():
        setattr(med, k, v)
    if data.schedules is not None:
        # Mevcut zamanlamalar korunur (geçmiş doz kayıtları onlara bağlı); eşleşmeyenler eklenir/çıkarılır.
        wanted = {(s.time_of_day, s.days_of_week) for s in data.schedules}
        for s in list(med.schedules):
            if (s.time_of_day, s.days_of_week) not in wanted:
                med.schedules.remove(s)
        existing = {(s.time_of_day, s.days_of_week) for s in med.schedules}
        for t, d in wanted - existing:
            med.schedules.append(MedicationSchedule(time_of_day=t, days_of_week=d))
    db.commit()
    db.refresh(med)
    return med


@router.delete("/{med_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_medication(patient_id: int, med_id: int, db: DB, user: CurrentUser):
    """Geçmiş uyum verisi kaybolmasın diye ilaç silinmez, pasife alınır."""
    get_patient_for_write(db, user, patient_id)
    med = _get_med(db, patient_id, med_id)
    med.active = False
    db.commit()
