"""TİTCK ruhsatlı ürün listesinden katalog üretimi + kürate liste çapraz kontrolü.

Gerçek TİTCK dosyasına bağımlı değildir: tests/fixtures/titck_sample.csv gerçek listenin sütun yapısını
(başlık 2. satırda, ATC sonunda boşluk, askı durumu sütunu) taşıyan temsili küçük bir örnektir. Üretilmiş
app/data/drug_catalog_tr.yaml ayrıca tutarlılık ve kürate katman çapraz kontrolünden geçer.
"""

from datetime import date
from pathlib import Path

import pytest
import yaml

from app.scripts import import_titck
from app.services import drug_catalog as dc
from app.services import titck

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "titck_sample.csv"
CURATED = dc.CATALOG_YAML.parent / "drug_catalog_curated.yaml"


@pytest.fixture(scope="module")
def sample():
    rows, total = titck.read_titck(SAMPLE)
    entries, report = titck.build_catalog(rows, CURATED)
    return rows, total, {e.generic: e for e in entries}, report


# ---------------------------------------------------------------- birimler
@pytest.mark.parametrize("product,brand", [
    ("GLİFOR 1000 MG 100 FİLM KAPLI TABLET", "Glifor"),
    ("GALVUS MET 50/1000 MG FİLM KAPLI TABLET, 60 ADET", "Galvus Met"),
    ("DIAMICRON MR 60 MG DEĞİŞTİRİLMİŞ SALIMLI TABLET", "Diamicron MR"),
    ("LANTUS SOLOSTAR 100 IU/ML ENJEKSİYONLUK ÇÖZELTİ", "Lantus"),
    ("FIASP® FLEXTOUCH® 100 U/ML", "Fiasp"),
    ("HUMALOG MIX25 100 IU/ML SÜSPANSİYON", "Humalog Mix"),
    ("CO-DIOVAN 160/12,5 MG FİLM TABLET", "Co-Diovan"),
    ("GLUCOTROL-XL 10 MG KONTROLLÜ SALIMLI TABLET", "Glucotrol-XL"),
    ("CONCOR 5 MG 30 LAK TABLET", "Concor"),
    ("% 0,4 LIDODEKS %5 DEKSTROZ İÇİNDE", None),
])
def test_brand_from_product(product, brand):
    assert titck.brand_from_product(product) == brand


@pytest.mark.parametrize("text,atc,expected", [
    ("metformin hidroklorür", "A10BA02", "metformin"),
    ("Sitagliptin fosfat monohidrat, Metformin hidroklorür", "A10BD07", "metformin + sitagliptin"),
    ("sitagliptin fosfat monohidrat metformin hidroklorür", "A10BD07", "metformin + sitagliptin"),  # ayırıcısız
    ("valsartan and diuretics", "C09DA03", "hydrochlorothiazide + valsartan"),  # ATC yedeği
    ("Valsartan, Klortalidon", "C09DA03", "chlorthalidone + valsartan"),  # metin ATC yedeğinden önce
    ("ramipril", "C09BA05", "hydrochlorothiazide + ramipril"),  # kombinasyon kodunda eksik yazım
    ("kandesartan sileksetilamlodipin besilathidroklorotiyazid", "C09DX06",
     "amlodipine + candesartan + hydrochlorothiazide"),
    ("asetilsalisilik asit", "B01AC06", "aspirin"),
    ("glibenklamid", "A10BB01", "glyburide"),
    ("s-amlodipin besilat", "C08CA01", "levamlodipine"),
    ("olmesartan medoksomil/amlodipin besilat/hidroklorotiyazid", "C09DX03",
     "amlodipine + hydrochlorothiazide + olmesartan"),
    ("%25 İnsülin lispro, %75İnsülin lispro protamin", "A10AD04", "insulin lispro + insulin lispro protamine"),
    ("İnsülin lispro", "A10AD04", "insulin lispro"),  # karışım kodlu ama karışım olmayan ürün
    ("biyosentetik insan insülini", "A10AD30", "insulin human + insulin isophane"),
    ("aspartam", "A10X", None),  # tatlandırıcı (yanlış ATC)
    ("setirizin dihidroklorür", "C09DA08", None),  # antihistaminik ARB koduyla listelenmiş
    ("", "C09CA01", None),
])
def test_ingredient_to_generic(text, atc, expected):
    assert titck.ingredient_to_generic(text, atc) == expected


def test_all_substance_keys_are_canonical():
    for generic in titck.SUBSTANCES:
        assert dc.canonical_generic(generic) == generic, generic
    for generic in (*titck.ATC_FORCED.values(), *titck.ATC_FALLBACK.values()):
        assert all(c in titck.SUBSTANCES for c in generic.split(" + ")), generic


@pytest.mark.parametrize("atc,label", [
    ("A10BA02", "Biguanid (oral antidiyabetik)"),
    ("A10BX16", "GIP/GLP-1 reseptör agonisti"),
    ("B01AC04", "Antiagregan (P2Y12 inhibitörü)"),
    ("C09DA03", "ARB + diüretik"),
    ("C10AA07 ", "Statin (HMG-KoA redüktaz inhibitörü)"),
    ("H02AB02", None),
])
def test_atc_class(atc, label):
    assert titck.atc_class(atc) == label


def test_list_date_from_name():
    name = "RuhsatlBeeriTbbirnlerListesi23.10.2026_d18f65a1-2d88-4f9c-b2af-f7414893c40c.xlsx"
    assert titck.list_date_from_name(name) == date(2026, 10, 23)
    assert titck.list_date_from_name("liste.xlsx") is None


# ---------------------------------------------------------------- örnek fikstürden üretim
def test_read_filters_by_atc_and_reads_suspension(sample):
    rows, total, _, _ = sample
    assert total == 25
    assert all(r.atc.startswith(titck.TARGET_ATC_PREFIXES) for r in rows)
    assert not any("ONADRON" in r.product for r in rows)  # H02 filtre dışı
    assert [r.product for r in rows if r.suspended] == ["ESKİMARKA 10 MG TABLET"]
    assert all(r.atc == r.atc.strip() for r in rows)


def test_build_catalog_from_sample(sample):
    _, _, entries, report = sample
    met = entries["metformin"]
    assert met.brands[:1] == ["Glifor"] and "Glifor SR" in met.brands
    assert "Metformin Teva" not in met.brands  # etken madde adıyla başlayan jenerik ürün
    assert met.atc == ["A10BA02"] and met.pharm_class == "Biguanid (oral antidiyabetik)" and met.products == 3
    assert entries["metformin + vildagliptin"].brands == ["Galvus Met"]
    assert entries["gliclazide"].brands == ["Diamicron", "Diamicron MR"]  # kürate yazım; marka ailesi ön eki
    assert entries["insulin glargine"].brands == ["Lantus"]
    assert entries["insulin lispro"].brands == ["Humalog"]
    assert entries["insulin lispro + insulin lispro protamine"].brands == ["Humalog Mix"]
    assert entries["hydrochlorothiazide + valsartan"].brands == ["Co-Diovan"]
    assert entries["chlorthalidone + valsartan"].display == "Klortalidon + Valsartan"
    assert entries["levamlodipine"].brands == ["Amlofull"]
    asp = entries["aspirin"]
    assert asp.brands == ["Coraspin"] and "Asetilsalisilik asit" in asp.aliases
    assert entries["ramipril"].brands == ["Delix"] and entries["hydrochlorothiazide + ramipril"].brands == ["Delix Plus"]
    assert entries["rosuvastatin"].products == 0 and entries["rosuvastatin"].brands == []  # tek ürünü askıda
    assert report.suspended_skipped == 1
    assert set(report.unresolved) == {("A10X", "aspartam"), ("C09DA08", "setirizin dihidroklorür")}
    # Örnekte olmayan kürate markalar raporlanır (gerçek listede bulunurlar)
    assert ("bisoprolol", "Concor") not in report.curated_missing_brands
    assert ("amlodipine", "Norvasc") not in report.curated_missing_brands
    assert ("rosuvastatin", "Crestor") in report.curated_missing_brands


def test_xlsx_input_matches_csv(tmp_path, sample):
    openpyxl = pytest.importorskip("openpyxl")
    import csv

    wb = openpyxl.Workbook()
    ws = wb.active
    with open(SAMPLE, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter=";"):
            ws.append([int(c) if c.isdigit() and len(c) < 4 else c for c in row])
    path = tmp_path / "RuhsatlBeeriTbbirnlerListesi23.10.2026_test.xlsx"
    wb.save(path)
    rows, total = titck.read_titck(path)
    assert (rows, total) == (sample[0], sample[1])


def test_cli_writes_yaml_with_source_header(tmp_path, db, capsys):
    out = tmp_path / "katalog.yaml"
    assert import_titck.main([str(SAMPLE), "--list-date", "23.10.2026", "--output", str(out), "--report",
                              "--update-db"], session_factory=lambda: db) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Türkçe ilaç kataloğu — OTOMATİK ÜRETİLMİŞTİR")
    assert "# Kaynak: TİTCK, 23.10.2026" in text and "# Üretim: python -m app.scripts.import_titck" in text
    drugs = {d.generic: d for d in dc.load_catalog_yaml(out)}
    assert drugs["metformin + sitagliptin"].brands == ("Janumet",)
    printed = capsys.readouterr().out
    assert "çözülemedi: A10X 'aspartam'" in printed and "Veritabanı:" in printed
    from sqlalchemy import select

    from app.models import DrugCatalog
    row = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "levamlodipine"))
    assert row is not None and row.tr_brand_names == ["Amlofull"]


def test_cli_missing_file(tmp_path, capsys):
    assert import_titck.main([str(tmp_path / "yok.xlsx"), "--output", str(tmp_path / "x.yaml")]) == 2
    assert "bulunamadı" in capsys.readouterr().err


# ---------------------------------------------------------------- üretilmiş katalog: tutarlılık + çapraz kontrol
@pytest.fixture(scope="module")
def generated():
    text = dc.CATALOG_YAML.read_text(encoding="utf-8")
    return text, yaml.safe_load(text), {d.generic: d for d in dc.load_catalog_yaml()}


def test_generated_yaml_header(generated):
    text, _, _ = generated
    head = text.splitlines()[:6]
    assert any(line.startswith("# Kaynak: TİTCK, ") and "dinamikmodul/85" in line for line in head)
    assert any(line.startswith("# Üretim: python -m app.scripts.import_titck") for line in head)
    assert titck.list_date_from_name(head[1]) is not None


def test_generated_yaml_consistency(generated):
    _, raw, drugs = generated
    assert len(raw) == len(drugs) >= 150  # yinelenen etken madde yok (load_catalog_yaml yineleneni reddeder)
    seen_brands: dict[str, str] = {}
    for it in raw:
        g = it["generic"]
        assert dc.canonical_generic(g) == g, g
        assert all(c in titck.SUBSTANCES for c in g.split(" + ")), g
        for code in it.get("atc") or []:
            assert code.startswith(titck.TARGET_ATC_PREFIXES) and code == code.strip().upper(), (g, code)
        if it.get("atc"):
            assert it.get("pharm_class"), g
            assert it.get("products", 0) > 0, g
        for b in it["brands"]:
            key = dc.normalize(b)
            assert key not in seen_brands, f"{b}: {seen_brands.get(key)} ve {g}"
            seen_brands[key] = g


def test_curated_layer_cross_check(generated):
    """Kürate listedeki her (TR marka, etken madde) çifti TİTCK'dan üretilmiş katalogda bulunmalı."""
    _, _, drugs = generated
    with open(CURATED, encoding="utf-8") as f:
        curated = yaml.safe_load(f)
    missing = []
    for it in curated:
        g = dc.canonical_generic(it["generic"])
        assert g in drugs, f"kürate etken madde üretilmiş katalogda yok: {g}"
        for b in it.get("brands") or []:
            if b not in drugs[g].brands:
                missing.append((g, b))
        for a in it.get("aliases") or []:
            assert a in drugs[g].aliases, (g, a)
    assert not missing, f"TİTCK'da bulunmayan kürate markalar (aliases'a taşıyın ya da düzeltin): {missing}"


def test_demo_and_key_brands_resolve(generated):
    _, _, drugs = generated
    for name, generic in [("Glifor", "metformin"), ("Norvasc", "amlodipine"), ("Coversyl", "perindopril"),
                          ("Galvus Met", "metformin + vildagliptin"), ("Concor", "bisoprolol"),
                          ("Janumet", "metformin + sitagliptin"), ("Coraspin", "aspirin")]:
        assert dc.resolve_name(name) == generic, name
        assert name in drugs[generic].brands
