"""openFDA ayrıştırma testleri: gerçek API yanıtlarından kesitler (tests/fixtures/openfda_*.json, 2026-10-26).

Elle yazılmış sahte yanıtlar yerine gerçek yanıt biçimi kullanılır; openFDA alan adları ya da yanıt yapısı
değişirse önce haftalık canlı kontrol (tests/test_openfda_live.py, .github/workflows/openfda-smoke.yml) kırılır,
ardından bu fikstürler yenilenir (docs/openfda-senkron.md).
"""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models import DrugCatalog
from app.services import drug_catalog as dc
from app.services import openfda

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    with open(FIXTURES / f"openfda_{name}.json", encoding="utf-8") as f:
        return json.load(f)


def _response(name: str) -> httpx.Response:
    fx = _fixture(name)
    return httpx.Response(fx["status"], json=fx["body"])


def test_fixtures_are_real_openfda_shapes():
    for name in ("count_dpp4_generics", "count_metformin_brands", "count_metformin_sitagliptin_brands"):
        body = _fixture(name)["body"]
        assert "disclaimer" in body["meta"] and body["results"]
        assert all(set(r) == {"term", "count"} for r in body["results"])
    assert _fixture("not_found")["status"] == 404
    assert _fixture("not_found")["body"]["error"]["code"] == "NOT_FOUND"


def test_parse_real_label():
    label = _fixture("label_metformin")["body"]["results"][0]
    parsed = openfda.parse_label("Janumet", "metformin + sitagliptin", label)
    assert parsed["brand_names"] == label["openfda"]["brand_name"][:5]
    assert "SITAGLIPTIN" in parsed["active_ingredients"]
    assert parsed["indications"] and len(parsed["indications"]) <= openfda.MAX_FIELD_CHARS + 1
    assert parsed["warnings"] and parsed["drug_interactions"]
    assert parsed["set_id"] == label["set_id"]
    assert "hekiminize danışın" in parsed["disclaimer"]
    # Doz bilgisi hiçbir zaman yanıta taşınmaz (koç doz önermez)
    assert not any("dosage" in k for k in parsed)


def test_fetch_label_with_real_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == 'openfda.generic_name:"metformin"'
        return _response("label_metformin")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        data = openfda.fetch_label("Glifor 1000 mg", client=c)
    assert data["resolved_generic"] == "metformin" and data["indications"]


def test_fetch_label_real_not_found():
    with httpx.Client(transport=httpx.MockTransport(lambda r: _response("not_found"))) as c:
        with pytest.raises(openfda.DrugLabelNotFound):
            openfda.fetch_label("Glifor", client=c)


def _real_openfda(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    search, count = p["search"], p["count"]
    if count == "openfda.generic_name.exact" and "Dipeptidyl Peptidase 4" in search:
        return _response("count_dpp4_generics")
    if count == "openfda.brand_name.exact" and search == 'openfda.generic_name:"metformin"':
        return _response("count_metformin_brands")
    if count == "openfda.brand_name.exact" and search == dc.generic_search_expr("metformin + sitagliptin"):
        return _response("count_metformin_sitagliptin_brands")
    return _response("not_found")


def test_sync_with_real_responses(db):
    with httpx.Client(transport=httpx.MockTransport(_real_openfda)) as c:
        report = dc.sync_from_openfda(db, client=c, sleeper=lambda s: None, sleep=0,
                                      classes=[("Dipeptidyl Peptidase 4 Inhibitor [EPC]", "DPP-4 inhibitörü")])
    rows = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
    # Keşif: "SITAGLIPTIN HYDROCHLORIDE ORAL" -> "sitagliptin"; üçlü kombinasyon yeni satır olarak eklenir
    assert {"sitagliptin", "linagliptin", "metformin + sitagliptin",
            "empagliflozin + linagliptin + metformin"} <= set(rows)
    triple = rows["empagliflozin + linagliptin + metformin"]
    assert triple.source == "openfda" and triple.pharm_class == "DPP-4 inhibitörü"
    assert report.discovered == 6 and not report.failed_queries
    # Markalar: metformin sorgusu kombinasyon markalarını da döndürür; en çok bileşenli sahibe bağlanır
    ms = rows["metformin + sitagliptin"]
    assert {"janumet", "janumet xr", "zituvimet", "zituvimet xr"} <= {b.lower() for b in ms.brand_names}
    met = rows["metformin"]
    assert met.openfda_available is True and "Glifor" in met.tr_brand_names
    assert not {"Janumet", "Zituvimet"} & set(met.brand_names)
    assert not any(b.lower().startswith("metformin") for b in met.brand_names if b not in met.tr_brand_names)
    # openFDA'da etiketi olmayan TR etken maddesi (gliklazid: gerçek yanıt 404) işaretlenir
    assert rows["gliclazide"].openfda_available is False and "gliclazide" in report.unavailable


@pytest.mark.parametrize("term", [
    # Gerçek senkronda (2026-10-26) EPC sınıflarından gelen gürültü örnekleri
    "attention deficit mild drops", "water", "diluent", "homeopathic liquid", "bayer aspirin extra strength",
    "alka-seltzer pain relief", "aspirin + caffeine + dextromethorphan", "acetaminophen aspirin caffeine",
    "glyburide-metformin", "pancreas db liquescence",
])
def test_discovery_noise_is_filtered(term):
    g = dc._clean_discovered(dc.canonical_generic(term))
    known = {"aspirin", "metformin", "glyburide", "amlodipine", "valsartan"}
    assert g not in dc._filter_discovered({g: "x"}, known)


@pytest.mark.parametrize("term,expected", [
    ("BUMETANIDE", "bumetanide"),
    ("INSULIN GLARGINE-YFGN", "insulin glargine"),  # FDA biyobenzer son eki atılır
    ("ASPIRIN AND DIPYRIDAMOLE", "aspirin + dipyridamole"),
    ("SOTAGLIFLOZIN", "sotagliflozin"),
])
def test_discovery_keeps_real_drugs(term, expected):
    g = dc._clean_discovered(dc.canonical_generic(term))
    assert g == expected
    assert g in dc._filter_discovered({g: "x", "dipyridamole": "x"}, {"aspirin"})
