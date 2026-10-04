"""openFDA Drug Label API istemcisi.

openFDA ABD etiketlerini döndürür; Türkiye'deki ticari isimler bulunmaz. Bu yüzden önce
TR ticari isim -> etken madde eşlemesi yapılır, sorgu etken madde (generic_name) üzerinden atılır.
"""

import json
import logging

import httpx

from app.config import get_settings
from app.redis_client import get_redis
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
CACHE_TTL_SECONDS = 24 * 3600

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------- önbellek
class LabelCache:
    """openFDA etiket önbelleği: ortak Redis istemcisi (app.redis_client.get_redis) varsa Redis (24 saat TTL,
    JSON metin; istemci decode_responses=True), değilse süreç içi sözlük.

    Redis'e ulaşılamazsa sessizce süreç içi önbelleğe düşer; önbellek hatası isteği asla bozmaz."""

    PREFIX = "openfda:label:"

    def __init__(self) -> None:
        self._memory: dict[str, dict] = {}

    @staticmethod
    def _client():
        return get_redis()

    def get(self, key: str) -> dict | None:
        r = self._client()
        if r is not None:
            try:
                raw = r.get(self.PREFIX + key)
                return json.loads(raw) if raw else None
            except Exception:
                log.warning("Redis okunamadı; süreç içi önbellek kullanılıyor", exc_info=True)
        return self._memory.get(key)

    def set(self, key: str, value: dict) -> None:
        r = self._client()
        if r is not None:
            try:
                r.setex(self.PREFIX + key, CACHE_TTL_SECONDS, json.dumps(value, ensure_ascii=False))
                return
            except Exception:
                log.warning("Redis'e yazılamadı; süreç içi önbellek kullanılıyor", exc_info=True)
        self._memory[key] = value

    def clear(self) -> None:
        self._memory.clear()
        r = self._client()
        if r is not None:
            try:
                for k in r.scan_iter(self.PREFIX + "*"):
                    r.delete(k)
            except Exception:
                log.warning("Redis önbelleği temizlenemedi", exc_info=True)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


label_cache = LabelCache()


def get_label_cached(name: str) -> dict:
    """Önbellekli fetch_label. DrugLabelNotFound / OpenFDAUnavailable yukarı fırlatılır."""
    key = resolve_generic(name)
    cached = label_cache.get(key) if key else None
    if cached is not None:
        return cached
    data = fetch_label(name)  # modül düzeyinde çözülür: testler monkeypatch edebilir
    label_cache.set(key, data)
    return data
