from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import CurrentUser
from app.services import openfda

router = APIRouter(prefix="/drugs", tags=["drugs (openFDA)"])

_cache: dict[str, dict] = {}  # M2: süreç içi önbellek; M3'te Redis'e taşınacak


@router.get("/label")
def drug_label(name: Annotated[str, Query(min_length=2, max_length=80)], user: CurrentUser):
    """İlaç adından (TR ticari isim veya etken madde) openFDA etiket bilgisi: etken madde, endikasyon,
    uyarılar, etkileşimler. Yalnızca eğitim amaçlıdır."""
    key = openfda.resolve_generic(name)
    if key in _cache:
        return _cache[key]
    try:
        data = openfda.fetch_label(name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    _cache[key] = data
    return data
