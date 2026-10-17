"""Spec yolları: `GET /v1/medications|doses|measurements?patient_id=`.

Hasta-merkezli yolların (`/v1/patients/{id}/...`) düz karşılıklarıdır; aynı yetki kuralları geçerlidir
(yetkisiz ya da bilinmeyen hasta → 404). Hasta rolünde `patient_id` verilmezse kendi kaydı kullanılır.
Yalnızca `/v1` altında yayınlanır.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.deps import DB, CurrentUser, get_own_patient
from app.models import MeasurementType, Role
from app.pagination import MAX_PAGE
from app.routers import doses, measurements, medications
from app.schemas import DoseLogOut, MeasurementOut, MedicationOut

router = APIRouter(tags=["collections"])

PatientId = Annotated[int | None, Query(description="Hasta kimliği (hasta rolünde boş bırakılabilir)")]
Limit = Annotated[int | None, Query(ge=1, le=MAX_PAGE, description="Sayfa boyutu (cursor sayfalama)")]
Cursor = Annotated[str | None, Query(description="Önceki yanıtın X-Next-Cursor değeri")]


def _patient_id(db, user, patient_id: int | None) -> int:
    if patient_id is not None:
        return patient_id
    if user.role == Role.patient:
        return get_own_patient(db, user).id
    raise HTTPException(422, "patient_id gerekli")


@router.get("/medications", response_model=list[MedicationOut])
def list_medications(db: DB, user: CurrentUser, patient_id: PatientId = None, include_inactive: bool = False):
    return medications.list_medications(_patient_id(db, user, patient_id), db, user, include_inactive)


@router.get("/doses", response_model=list[DoseLogOut])
def list_doses(db: DB, user: CurrentUser, request: Request, response: Response, patient_id: PatientId = None,
               days: Annotated[int, Query(ge=1, le=90)] = 30, limit: Limit = None, cursor: Cursor = None):
    return doses.list_doses(_patient_id(db, user, patient_id), db, user, request, response, days, limit, cursor)


@router.get("/measurements", response_model=list[MeasurementOut])
def list_measurements(db: DB, user: CurrentUser, request: Request, response: Response, patient_id: PatientId = None,
                      type: MeasurementType | None = None, days: Annotated[int, Query(ge=1, le=365)] = 30,
                      limit: Limit = None, cursor: Cursor = None):
    return measurements.list_measurements(_patient_id(db, user, patient_id), db, user, request, response, type,
                                          days, limit, cursor)
