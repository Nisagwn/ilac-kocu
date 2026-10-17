"""İlaç kataloğu: kürate TR ticari ad listesi + openFDA senkronu, Türkçe karakter duyarsız arama.

Kaynaklar:
- `app/data/drug_catalog_tr.yaml`: TR ticari ad -> etken madde (tek doğruluk kaynağı; resolve_generic buradan beslenir)
- openFDA `count` sorgusu: diyabet/hipertansiyon EPC sınıflarındaki etken maddeler (`sync_openfda`)

CLI:
    python -m app.services.drug_catalog curated   # YAML'ı tabloya yükler (idempotent)
    python -m app.services.drug_catalog openfda   # openFDA'dan etken madde listesini çeker (ağ gerekir)
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DrugCatalog
from app.services.guardrails import normalize

log = logging.getLogger(__name__)

CATALOG_YAML = Path(__file__).resolve().parent.parent / "data" / "drug_catalog_tr.yaml"
GROUPS = ("diabetes", "hypertension", "other")

# openFDA EPC sınıfları -> endikasyon grubu (senkron bu sınıflardaki etken maddeleri çeker)
EPC_CLASSES: dict[str, str] = {
    "Biguanide [EPC]": "diabetes",
    "Sulfonylurea [EPC]": "diabetes",
    "Dipeptidyl Peptidase 4 Inhibitor [EPC]": "diabetes",
    "Sodium-Glucose Cotransporter 2 Inhibitor [EPC]": "diabetes",
    "GLP-1 Receptor Agonist [EPC]": "diabetes",
    "Thiazolidinedione [EPC]": "diabetes",
    "Insulin Analog [EPC]": "diabetes",
    "Angiotensin Converting Enzyme Inhibitor [EPC]": "hypertension",
    "Angiotensin 2 Receptor Blocker [EPC]": "hypertension",
    "Dihydropyridine Calcium Channel Blocker [EPC]": "hypertension",
    "beta-Adrenergic Blocker [EPC]": "hypertension",
    "Thiazide Diuretic [EPC]": "hypertension",
    "Loop Diuretic [EPC]": "hypertension",
}

# openFDA generic_name'deki tuz/ester ekleri: "METFORMIN HYDROCHLORIDE" ve "metformin" aynı katalog satırıdır
_SALT_WORDS = {
    "hydrochloride", "hcl", "besylate", "besilate", "maleate", "mesylate", "tartrate", "succinate", "fumarate",
    "erbumine", "arginine", "medoxomil", "cilexetil", "potassium", "sodium", "calcium", "magnesium", "dihydrate",
    "monohydrate", "hydrobromide", "sulfate", "phosphate", "acetate", "citrate", "anhydrous", "extended", "release",
}
_DOSE_TOKEN = re.compile(r"\d")
_UNIT_WORDS = {"mg", "mcg", "g", "ml", "iu", "tablet", "tb", "tab", "kapsul", "film", "mr", "xr", "sr"}


def canonical_generic(name: str) -> str:
    """Etken madde adını katalog anahtarına indirger: küçük harf, Türkçe duyarsız, tuz/doz ekleri atılmış."""
    tokens = [t for t in re.split(r"[\s,/]+", normalize(name)) if t]
    kept = [t for t in tokens if t not in _SALT_WORDS and t not in _UNIT_WORDS and not _DOSE_TOKEN.search(t)]
    return " ".join(kept)


def search_text_for(generic: str, brands: list[str]) -> str:
    return " | ".join(normalize(x) for x in [generic, *brands])


# ---------------------------------------------------------------- kürate liste (YAML)
def load_curated_yaml(path: Path = CATALOG_YAML) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    seen: set[str] = set()
    for it in items:
        if not it.get("generic") or not isinstance(it.get("brands"), list):
            raise ValueError(f"drug_catalog_tr.yaml: geçersiz kayıt {it!r}")
        if it.get("group", "other") not in GROUPS:
            raise ValueError(f"drug_catalog_tr.yaml: '{it['generic']}' bilinmeyen grup {it.get('group')!r}")
        key = canonical_generic(it["generic"])
        if key in seen:
            raise ValueError(f"drug_catalog_tr.yaml: '{key}' iki kez tanımlı")
        seen.add(key)
    return items


@lru_cache
def brand_to_generic() -> dict[str, str]:
    """Normalize edilmiş TR ticari ad (ve etken maddenin kendisi) -> etken madde."""
    out: dict[str, str] = {}
    for it in load_curated_yaml():
        generic = canonical_generic(it["generic"])
        out[generic] = generic
        for b in it["brands"]:
            out[normalize(b)] = generic
    return out


def upsert_curated(db: Session, path: Path = CATALOG_YAML) -> tuple[int, int]:
    """YAML'ı drug_catalog'a etken maddeye göre upsert eder (commit eder). openFDA satırlarının ticari adları
    YAML'dakilerle değiştirilir; YAML'da olmayan satırlar silinmez (ilaçlar onlara bağlı olabilir).

    Döndürür: (eklenen, güncellenen)."""
    existing = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
    created = updated = 0
    for it in load_curated_yaml(path):
        generic = canonical_generic(it["generic"])
        brands = list(it["brands"])
        values = {
            "brand_names": brands,
            "indication_group": it.get("group", "other"),
            "pharm_class": it.get("pharm_class"),
            "source": "curated",
            "search_text": search_text_for(generic, brands),
        }
        row = existing.get(generic)
        if row is None:
            db.add(DrugCatalog(generic_name=generic, **values))
            created += 1
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            updated += 1
    db.commit()
    return created, updated


def ensure_catalog(db: Session) -> None:
    """Tablo boşsa kürate listeyi yükler (ilk kurulum ve testler; seed de çağırır)."""
    if db.scalar(select(func.count(DrugCatalog.id))) == 0:
        upsert_curated(db)


# ---------------------------------------------------------------- openFDA senkronu
def fetch_epc_generics(epc: str, client: httpx.Client) -> list[tuple[str, int]]:
    """Bir EPC sınıfındaki etken maddeler ve etiket sayıları (openFDA `count`). Eşleşme yoksa boş liste."""
    settings = get_settings()
    params = {"search": f'openfda.pharm_class_epc:"{epc}"', "count": "openfda.generic_name.exact", "limit": 100}
    if settings.openfda_api_key:
        params["api_key"] = settings.openfda_api_key
    resp = client.get(settings.openfda_base_url, params=params)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return [(r["term"], int(r["count"])) for r in resp.json().get("results") or [] if r.get("term")]


def sync_openfda(db: Session, client: httpx.Client | None = None,
                 classes: dict[str, str] | None = None) -> tuple[int, int]:
    """openFDA'daki diyabet/hipertansiyon etken maddelerini kataloğa ekler; var olanların etiket sayısını
    günceller. Çok bileşenli kombinasyonlar ("x and y") atlanır. Kürate satırların grubu/adları korunur.

    Döndürür: (eklenen, güncellenen). Ağ hatasında httpx.HTTPError fırlatır."""
    classes = classes or EPC_CLASSES
    own_client = client is None
    client = client or httpx.Client(timeout=15)
    counts: dict[str, tuple[int, str, str]] = {}  # generic -> (etiket sayısı, grup, EPC)
    try:
        for epc, group in classes.items():
            for term, count in fetch_epc_generics(epc, client):
                if " and " in term.lower() or "," in term:
                    continue
                key = canonical_generic(term)
                if not key:
                    continue
                prev = counts.get(key)
                counts[key] = ((prev[0] if prev else 0) + count, group, epc)
    finally:
        if own_client:
            client.close()

    existing = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
    created = updated = 0
    for key, (count, group, epc) in counts.items():
        row = existing.get(key)
        if row is None:
            db.add(DrugCatalog(generic_name=key, brand_names=[], indication_group=group, pharm_class=epc,
                               source="openfda", label_count=count, search_text=search_text_for(key, [])))
            created += 1
        elif row.label_count != count:
            row.label_count = count
            row.pharm_class = row.pharm_class or epc
            updated += 1
    db.commit()
    return created, updated


# ---------------------------------------------------------------- arama
@dataclass
class CatalogHit:
    id: int
    generic_name: str
    brand: str | None  # sorguyla eşleşen ticari ad (yoksa None: etken madde eşleşmesi)
    brand_names: list[str]
    indication_group: str
    pharm_class: str | None
    source: str

    @property
    def display_name(self) -> str:
        return f"{self.brand} ({self.generic_name})" if self.brand else self.generic_name


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _rank(q: str, generic: str, brands: list[str]) -> tuple[int, str | None]:
    """0: ticari ad başlangıcı, 1: etken madde başlangıcı, 2: kelime başlangıcı, 3: içinde geçiyor."""
    nb = [(normalize(b), b) for b in brands]
    for n, b in nb:
        if n.startswith(q):
            return 0, b
    if generic.startswith(q):
        return 1, None
    for n, b in nb:
        if any(w.startswith(q) for w in n.split()):
            return 2, b
    if any(w.startswith(q) for w in generic.split()):
        return 2, None
    for n, b in nb:
        if q in n:
            return 3, b
    return 3, None


def search_catalog(db: Session, q: str, limit: int = 10) -> list[CatalogHit]:
    nq = normalize(q)
    if len(nq) < 2:
        return []
    ensure_catalog(db)
    rows = db.scalars(
        select(DrugCatalog).where(DrugCatalog.search_text.like(f"%{_escape_like(nq)}%", escape="\\"))
    ).all()
    scored = []
    for r in rows:
        rank, brand = _rank(nq, r.generic_name, r.brand_names or [])
        scored.append(((rank, r.source != "curated", -(r.label_count or 0), brand or r.generic_name), r, brand))
    scored.sort(key=lambda t: t[0])
    return [
        CatalogHit(id=r.id, generic_name=r.generic_name, brand=brand, brand_names=list(r.brand_names or []),
                   indication_group=r.indication_group, pharm_class=r.pharm_class, source=r.source)
        for _, r, brand in scored[:limit]
    ]


def match_exact(db: Session, name: str) -> DrugCatalog | None:
    """Ad bir ticari adla ya da etken maddeyle birebir eşleşiyorsa katalog satırı (doz eki yok sayılır)."""
    key = canonical_generic(name)
    if not key:
        return None
    ensure_catalog(db)
    generic = brand_to_generic().get(key) or brand_to_generic().get(key.split()[0]) or key
    return db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == generic))


def main(argv: list[str]) -> int:
    from app.db import SessionLocal

    cmd = argv[0] if argv else ""
    if cmd not in ("curated", "openfda"):
        print(__doc__)
        return 2
    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    try:
        if cmd == "curated":
            created, updated = upsert_curated(db)
        else:
            upsert_curated(db)  # önce kürate adlar: openFDA satırları onlarla birleşsin
            created, updated = sync_openfda(db)
    finally:
        db.close()
    print(f"İlaç kataloğu ({cmd}): {created} eklendi, {updated} güncellendi.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
