"""openFDA Drug Label API istemcisi.

openFDA ABD etiketlerini döndürür; Türkiye'deki ticari isimler bulunmaz. Bu yüzden önce
TR ticari isim -> etken madde eşlemesi yapılır, sorgu etken madde (generic_name) üzerinden atılır.
"""

import httpx

from app.config import get_settings
from app.services.guardrails import normalize

# Küçük, kürate eşleme tablosu (genişletilebilir). Anahtarlar normalize edilmiş.
TR_BRAND_TO_GENERIC = {
    "glifor": "metformin",
    "glucophage": "metformin",
    "matofin": "metformin",
    "diaformin": "metformin",
    "amaryl": "glimepiride",
    "diamicron": "gliclazide",
    "januvia": "sitagliptin",
    "galvus": "vildagliptin",
    "jardiance": "empagliflozin",
    "forziga": "dapagliflozin",
    "norvasc": "amlodipine",
    "coversyl": "perindopril",
    "delix": "ramipril",
    "beloc": "metoprolol",
    "concor": "bisoprolol",
    "micardis": "telmisartan",
    "diovan": "valsartan",
    "atacand": "candesartan",
    "lasix": "furosemide",
    "lipitor": "atorvastatin",
    "ator": "atorvastatin",
}

MAX_FIELD_CHARS = 1200


class DrugLabelNotFound(Exception):
    pass


class OpenFDAUnavailable(Exception):
    pass


def resolve_generic(name: str) -> str:
    key = normalize(name).split()[0] if name.strip() else ""
    return TR_BRAND_TO_GENERIC.get(key, key)


def _first(label: dict, field: str) -> str | None:
    val = label.get(field)
    if isinstance(val, list) and val:
        text = " ".join(val)
        return text[:MAX_FIELD_CHARS] + ("…" if len(text) > MAX_FIELD_CHARS else "")
    return None


def parse_label(query: str, generic: str, label: dict) -> dict:
    ofda = label.get("openfda", {})
    return {
        "query": query,
        "resolved_generic": generic,
        "brand_names": ofda.get("brand_name", [])[:5],
        "generic_names": ofda.get("generic_name", [])[:3],
        "active_ingredients": ofda.get("substance_name", []) or ofda.get("generic_name", []),
        "indications": _first(label, "indications_and_usage"),
        "warnings": _first(label, "warnings_and_cautions") or _first(label, "warnings"),
        "drug_interactions": _first(label, "drug_interactions"),
        "source_name": "openFDA Drug Label API (ABD FDA etiketi)",
        "source_url": f"https://api.fda.gov/drug/label.json?search=openfda.generic_name:\"{generic}\"",
        "set_id": label.get("set_id"),
        "disclaimer": "Bu bilgi ABD ilaç etiketinden alınmıştır, eğitim amaçlıdır; tedavi kararı için hekiminize danışın.",
    }


def fetch_label(name: str, client: httpx.Client | None = None) -> dict:
    settings = get_settings()
    generic = resolve_generic(name)
    if not generic:
        raise DrugLabelNotFound(name)

    params = {"search": f'openfda.generic_name:"{generic}"', "limit": 1}
    if settings.openfda_api_key:
        params["api_key"] = settings.openfda_api_key

    own_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.get(settings.openfda_base_url, params=params)
    except httpx.HTTPError as e:
        raise OpenFDAUnavailable(str(e)) from e
    finally:
        if own_client:
            client.close()

    if resp.status_code == 404:
        raise DrugLabelNotFound(name)
    if resp.status_code >= 400:
        raise OpenFDAUnavailable(f"openFDA HTTP {resp.status_code}")

    results = resp.json().get("results") or []
    if not results:
        raise DrugLabelNotFound(name)
    return parse_label(name, generic, results[0])
