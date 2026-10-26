"""openFDA canlı erişim kontrolü (gerçek ağ isteği). Varsayılan koşuda ATLANIR.

Çalıştırma: `pytest -m openfda_live tests/test_openfda_live.py` (OPENFDA_API_KEY varsa kullanılır).
Haftalık + elle tetiklenen iş akışı: .github/workflows/openfda-smoke.yml. Amaç: katalog senkronunun ve etiket
çekmenin dayandığı alanlar (count `term`/`count`, `openfda.generic_name`, `brand_name`, etiket metin alanları)
hâlâ yerinde mi; kırılırsa tests/fixtures/openfda_*.json yenilenir. İstek sayısı küçük tutulur (4 istek).
"""

import httpx
import pytest

from app.config import get_settings
from app.services import drug_catalog as dc
from app.services import openfda

pytestmark = pytest.mark.openfda_live


def _params(**extra) -> dict:
    key = get_settings().openfda_api_key
    return {**extra, **({"api_key": key} if key else {})}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=60) as c:
        yield c


def _get(client: httpx.Client, **params) -> httpx.Response:
    for _ in range(3):  # openFDA zaman zaman geçici 500 döndürür (docs/openfda-senkron.md)
        resp = client.get(get_settings().openfda_base_url, params=_params(**params))
        if resp.status_code < 500:
            return resp
    return resp


def test_label_search_has_expected_fields(client):
    resp = _get(client, search=dc.generic_search_expr("metformin"), limit=1)
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert "disclaimer" in body["meta"] and body["meta"]["results"]["total"] > 0
    label = body["results"][0]
    assert {"generic_name", "brand_name"} <= set(label["openfda"])
    assert "indications_and_usage" in label and "set_id" in label
    parsed = openfda.parse_label("Glifor", "metformin", label)
    assert parsed["indications"] and parsed["generic_names"]


def test_count_endpoints_used_by_sync(client):
    resp = _get(client, search='openfda.pharm_class_epc:"Dipeptidyl Peptidase 4 Inhibitor [EPC]"',
                count="openfda.generic_name.exact", limit=100)
    assert resp.status_code == 200, resp.text[:300]
    terms = {dc.canonical_generic(r["term"]) for r in resp.json()["results"]}
    assert "sitagliptin" in terms and "metformin + sitagliptin" in terms

    resp = _get(client, search=dc.generic_search_expr("metformin + sitagliptin"),
                count="openfda.brand_name.exact", limit=100)
    assert resp.status_code == 200, resp.text[:300]
    results = resp.json()["results"]
    assert all({"term", "count"} <= set(r) for r in results)
    assert "janumet" in {r["term"].lower() for r in results}


def test_not_found_is_404(client):
    resp = _get(client, search='openfda.generic_name:"gliclazide"', count="openfda.brand_name.exact")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
