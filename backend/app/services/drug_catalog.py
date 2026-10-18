"""İlaç kataloğu: kürate Türkçe liste + isteğe bağlı openFDA senkronu + arama.

Neden: openFDA yalnızca ABD etiketlerini içerir; hasta "Glifor" ya da rastgele bir metin yazınca bilgi
gelmez. Katalog, ilaç adını serbest metin yerine doğrulanmış bir etken maddeye (generic) bağlar.

- Kürate liste (app/data/drug_catalog_tr.yaml): TR ticari ad -> etken madde. Çevrimdışı çalışır;
  `seed_catalog` ile tabloya upsert edilir (`python -m app.seed`). `resolve_generic` (openfda.py) de
  bu listeden beslenir.
- openFDA senkronu (`sync_from_openfda`, CLI: `python -m app.scripts.sync_drug_catalog`): ilgili EPC
  sınıflarındaki etken maddeleri `count=openfda.generic_name.exact` ile keşfeder, her etken madde için
  `count=openfda.brand_name.exact` ile ABD marka adlarını bağlar ve `openfda_available` bayrağını günceller.
- Arama (`search_catalog`): normalize edilmiş metinde ön ek + içerir araması; TR ticari adla da bulur.

Etken madde anahtarı (`canonical_generic`): küçük harf, tuz/form ekleri atılmış, kombinasyonlarda
bileşenler alfabetik sırada " + " ile birleşik: "SITAGLIPTIN AND METFORMIN HYDROCHLORIDE" ->
"metformin + sitagliptin".
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DrugCatalog, Medication, utcnow
from app.services.guardrails import normalize

log = logging.getLogger(__name__)

CATALOG_YAML = Path(__file__).resolve().parent.parent / "data" / "drug_catalog_tr.yaml"

SOURCE_TR = "tr_curated"
SOURCE_OPENFDA = "openfda"

# ----------------------------------------------------------------- etken madde normalizasyonu
# Etken madde adından atılan tuz/ester ekleri (openFDA generic_name alanında sık geçer)
_SALT_WORDS = {
    "hydrochloride", "hcl", "dihydrochloride", "besylate", "besilate", "maleate", "mesylate", "potassium",
    "sodium", "calcium", "magnesium", "tartrate", "succinate", "fumarate", "hemifumarate", "medoxomil",
    "cilexetil", "erbumine", "arginine", "tosylate", "bisulfate", "hydrobromide", "monohydrate", "dihydrate",
    "hemihydrate", "trihydrate", "anhydrous", "propanediol", "phosphate", "acetate", "citrate", "mesilate",
    "etexilate", "lysine", "benzoate",
}
# Form / salım şekli ifadeleri (etken madde değildir)
_FORM_PHRASES = [
    "extended release", "extended-release", "delayed release", "delayed-release", "orally disintegrating",
    "film coated", "film-coated", "for oral suspension", "oral suspension", "oral solution", "chewable",
    "tablets", "tablet", "capsules", "capsule", "injection", "injectable", "solution", "suspension",
    "oral", "kit", "usp", "er", "xr", "sr", "xl", "cr", "mr", "odt", "pen", "and",
]
_FORM_RE = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in sorted(_FORM_PHRASES, key=len, reverse=True)
                                         if p != "and") + r")\b")
_UNIT_WORDS = {"mg", "mcg", "g", "ml", "iu", "unit", "units", "mg/ml"}
_COMBO_SPLIT = re.compile(r"\s*(?:\+|/|,|;|\band\b|\bwith\b|\bve\b)\s*")
MAX_COMPONENTS = 4


def _clean_component(text: str) -> str:
    text = _FORM_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9 \-]", " ", text)
    words = [w for w in text.split()
             if w not in _SALT_WORDS and w not in _UNIT_WORDS and not re.fullmatch(r"[\d.\-]+", w)]
    if not words:  # yalnızca tuz kelimelerinden oluşuyorsa (ör. "potassium chloride") olduğu gibi bırak
        words = text.split()
    return " ".join(words).strip()


def canonical_components(name: str) -> list[str]:
    """Etken madde adını bileşenlerine ayırır (normalize, tuz/form ekleri atılmış, alfabetik, tekil)."""
    n = normalize(name)
    parts = [_clean_component(p) for p in _COMBO_SPLIT.split(n)]
    return sorted({p for p in parts if p})


def canonical_generic(name: str) -> str:
    """Katalog anahtarı: "Sitagliptin and Metformin HCl" -> "metformin + sitagliptin"."""
    return " + ".join(canonical_components(name))


def display_from_generic(generic: str) -> str:
    return " + ".join(c[:1].upper() + c[1:] for c in generic.split(" + "))


def generic_search_expr(generic: str) -> str:
    """openFDA `search` ifadesi. Tek bileşen: `openfda.generic_name:"metformin"` (ifade araması);
    kombinasyon: her bileşen ayrı ifade, AND ile (sıra/biçim farkından bağımsız eşleşir)."""
    comps = [c for c in generic.split(" + ") if c] or [generic]
    return " AND ".join(f'openfda.generic_name:"{c}"' for c in comps)


def build_search_text(generic: str, display: str, brands: Iterable[str]) -> str:
    """Normalize edilmiş, " | " ile ayrılmış arama metni (her alan başı bir "kelime başı" sınırıdır)."""
    seen: list[str] = []
    for t in [generic, display, *brands]:
        nt = normalize(t or "")
        if nt and nt not in seen:
            seen.append(nt)
    return "| " + " | ".join(seen) + " |"


# ----------------------------------------------------------------- kürate TR liste
@dataclass(frozen=True)
class CuratedDrug:
    generic: str
    display: str
    pharm_class: str | None
    brands: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)  # aranabilir ek adlar (ör. Türkçe etken madde adı)


def load_catalog_yaml(path: Path = CATALOG_YAML) -> list[CuratedDrug]:
    with open(path, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    out: dict[str, CuratedDrug] = {}
    for it in items:
        if not isinstance(it, dict) or not it.get("generic"):
            raise ValueError(f"drug_catalog_tr.yaml: 'generic' alanı eksik: {it!r}")
        generic = canonical_generic(str(it["generic"]))
        if not generic:
            raise ValueError(f"drug_catalog_tr.yaml: geçersiz etken madde: {it['generic']!r}")
        if generic in out:
            raise ValueError(f"drug_catalog_tr.yaml: '{generic}' iki kez tanımlı")
        brands = tuple(str(b).strip() for b in (it.get("brands") or []) if str(b).strip())
        display = str(it.get("display") or display_from_generic(" + ".join(
            p.strip() for p in str(it["generic"]).split("+"))))
        aliases = tuple(str(a).strip() for a in (it.get("aliases") or []) if str(a).strip())
        out[generic] = CuratedDrug(generic, display, it.get("pharm_class"), brands, aliases)
    return list(out.values())


@lru_cache(maxsize=1)
def curated_alias_map() -> dict[str, str]:
    """Normalize edilmiş ad (TR ticari ad, etken madde, görünen ad) -> etken madde anahtarı.

    resolve_generic için veritabanı gerektirmeyen (çevrimdışı) eşleme; YAML'dan bir kez yüklenir."""
    aliases: dict[str, str] = {}
    for d in load_catalog_yaml():
        for name in (d.generic, d.display, *d.brands, *d.aliases):
            key = normalize(name)
            if key:
                aliases.setdefault(key, d.generic)
    return aliases


def resolve_name(name: str) -> str:
    """Serbest metin ilaç adını etken madde anahtarına çevirir: "Glifor 1000 mg" -> "metformin",
    "Galvus Met" -> "metformin + vildagliptin". En uzun kelime ön eki eşleşmesi kazanır.

    Bilinmeyen adlarda geriye dönük uyum için ilk kelime döner (kombinasyon yazımı ise bileşen anahtarı)."""
    n = normalize(name)
    if not n:
        return ""
    aliases = curated_alias_map()
    if n in aliases:
        return aliases[n]
    words = n.split()
    for k in range(min(len(words), 5), 0, -1):
        hit = aliases.get(" ".join(words[:k]))
        if hit:
            return hit
    if _COMBO_SPLIT.search(n) and len(canonical_components(n)) > 1:
        return canonical_generic(n)
    return words[0]


# ----------------------------------------------------------------- tabloya yükleme
def _merge_brands(*lists: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        for b in lst or []:
            key = normalize(b)
            if key and key not in seen:
                seen.add(key)
                out.append(b)
    return out


@lru_cache(maxsize=1)
def _curated_aliases() -> dict[str, tuple[str, ...]]:
    return {d.generic: d.aliases for d in load_catalog_yaml()}


def _refresh_search_text(row: DrugCatalog) -> None:
    extra = _curated_aliases().get(row.generic_name, ())
    row.search_text = build_search_text(row.generic_name, row.display_name, [*(row.brand_names or []), *extra])


def seed_catalog(db: Session, path: Path = CATALOG_YAML) -> tuple[int, int]:
    """Kürate TR katalogu upsert eder (idempotent, çevrimdışı). openFDA'dan gelen ABD marka adları ve
    `openfda_available` korunur. Ardından katalogla eşleşen doğrulanmamış ilaçları bağlar. Commit eder.

    Döndürür: (eklenen, güncellenen)."""
    existing = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
    created = updated = 0
    for d in load_catalog_yaml(path):
        row = existing.get(d.generic)
        tr_brands = list(d.brands)
        if row is None:
            row = DrugCatalog(generic_name=d.generic, display_name=d.display, brand_names=tr_brands,
                              tr_brand_names=tr_brands, pharm_class=d.pharm_class, source=SOURCE_TR,
                              openfda_available=False)
            _refresh_search_text(row)
            db.add(row)
            created += 1
            continue
        us_brands = [b for b in (row.brand_names or []) if normalize(b) not in {normalize(x) for x in row.tr_brand_names or []}]
        new_values = {
            "display_name": d.display,
            "tr_brand_names": tr_brands,
            "brand_names": _merge_brands(tr_brands, us_brands),
            "pharm_class": d.pharm_class or row.pharm_class,
            "source": SOURCE_TR,
        }
        if any(getattr(row, k) != v for k, v in new_values.items()):
            for k, v in new_values.items():
                setattr(row, k, v)
            updated += 1
        _refresh_search_text(row)
    db.flush()
    link_unverified_medications(db)
    db.commit()
    return created, updated


def ensure_catalog(db: Session) -> None:
    """Katalog boşsa kürate listeyi yükler (seed çalıştırılmamış kurulumlarda arama yine çalışsın)."""
    if db.scalar(select(DrugCatalog.id).limit(1)) is None:
        seed_catalog(db)


def find_exact(db: Session, name: str) -> DrugCatalog | None:
    """Adı (ticari ad, etken madde ya da görünen ad) birebir eşleşen katalog satırı."""
    n = normalize(name or "")
    if not n:
        return None
    needle = f"| {n} |"
    for row in db.scalars(select(DrugCatalog).where(DrugCatalog.search_text.contains(needle, autoescape=True))):
        return row
    generic = curated_alias_map().get(n)
    if generic:
        return db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == generic))
    return None


def apply_catalog(med: Medication, entry: DrugCatalog | None) -> None:
    """İlacı katalog satırına bağlar (etken madde katalogdan gelir) ya da bağı kaldırır (doğrulanmamış)."""
    if entry is None:
        med.catalog_id, med.verified = None, False
        return
    med.catalog_id, med.verified = entry.id, True
    med.active_ingredient = entry.generic_name


def link_unverified_medications(db: Session) -> int:
    """Katalog bağı olmayan ilaçları ada göre birebir eşleştirip doğrular (eski kayıtlar ve demo verisi)."""
    linked = 0
    for med in db.scalars(select(Medication).where(Medication.catalog_id.is_(None))):
        entry = find_exact(db, med.name)
        if entry is not None:
            apply_catalog(med, entry)
            linked += 1
    return linked


# ----------------------------------------------------------------- arama
@dataclass
class SearchHit:
    entry: DrugCatalog
    matched_brand: str | None
    rank: int


def _match_rank(text: str, q: str) -> int | None:
    """0: tam eşleşme, 1: ön ek, 2: kelime başı, 3: içerir, None: yok."""
    if text == q:
        return 0
    if text.startswith(q):
        return 1
    if f" {q}" in text:
        return 2
    if q in text:
        return 3
    return None


def search_catalog(db: Session, query: str, limit: int = 10) -> list[SearchHit]:
    q = normalize(query)
    if len(q) < 2:
        return []
    rows = db.scalars(
        select(DrugCatalog).where(DrugCatalog.search_text.contains(q, autoescape=True)).limit(500)
    ).all()
    hits: list[SearchHit] = []
    for row in rows:
        best: tuple[int, str | None] | None = None
        tr = {normalize(b) for b in row.tr_brand_names or []}
        # TR ticari adlar önce: aynı sırada eşleşirse TR adı gösterilir
        brands = sorted(row.brand_names or [], key=lambda b: normalize(b) not in tr)
        for b in brands:
            r = _match_rank(normalize(b), q)
            if r is not None and (best is None or r < best[0]):
                best = (r, b)
        for t in (row.generic_name, row.display_name):
            r = _match_rank(normalize(t), q)
            if r is not None and (best is None or r <= best[0]):
                best = (r, None)
        if best is None:  # yalnızca alanlar arası sınırda eşleşti (ör. "| " içeren sorgu)
            continue
        hits.append(SearchHit(row, best[1], best[0]))
    hits.sort(key=lambda h: (h.rank, h.entry.source != SOURCE_TR, len(h.entry.display_name), h.entry.display_name))
    return hits[:limit]


# ----------------------------------------------------------------- openFDA senkronu
# (EPC sınıfı, Türkçe sınıf adı). openFDA'da birçok tekli etiketin pharm_class alanı boştur (ör. metformin);
# bu yüzden kürate listedeki etken maddeler ayrıca doğrudan sorgulanır.
EPC_CLASSES: list[tuple[str, str]] = [
    # Diyabet
    ("Sulfonylurea [EPC]", "Sülfonilüre (oral antidiyabetik)"),
    ("Dipeptidyl Peptidase 4 Inhibitor [EPC]", "DPP-4 inhibitörü"),
    ("Sodium-Glucose Cotransporter 2 Inhibitor [EPC]", "SGLT2 inhibitörü"),
    ("GLP-1 Receptor Agonist [EPC]", "GLP-1 reseptör agonisti"),
    ("Glucose-dependent Insulinotropic Polypeptide Receptor Agonist [EPC]", "GIP/GLP-1 reseptör agonisti"),
    ("Insulin [EPC]", "İnsülin"),
    ("Insulin Analog [EPC]", "İnsülin analoğu"),
    ("alpha-Glucosidase Inhibitor [EPC]", "Alfa-glukozidaz inhibitörü"),
    ("Thiazolidinedione [EPC]", "Tiyazolidindion (oral antidiyabetik)"),
    ("Peroxisome Proliferator Receptor gamma Agonist [EPC]", "Tiyazolidindion (oral antidiyabetik)"),
    # Hipertansiyon
    ("Angiotensin Converting Enzyme Inhibitor [EPC]", "ACE inhibitörü"),
    ("Angiotensin 2 Receptor Blocker [EPC]", "Anjiyotensin reseptör blokeri (ARB)"),
    ("beta-Adrenergic Blocker [EPC]", "Beta bloker"),
    ("Dihydropyridine Calcium Channel Blocker [EPC]", "Kalsiyum kanal blokeri"),
    ("Calcium Channel Blocker [EPC]", "Kalsiyum kanal blokeri"),
    ("Thiazide Diuretic [EPC]", "Tiyazid diüretik"),
    ("Thiazide-like Diuretic [EPC]", "Tiyazid benzeri diüretik"),
    ("Loop Diuretic [EPC]", "Kıvrım (loop) diüretiği"),
    ("Aldosterone Antagonist [EPC]", "Aldosteron antagonisti"),
    ("Potassium-sparing Diuretic [EPC]", "Potasyum tutucu diüretik"),
    ("alpha-Adrenergic Blocker [EPC]", "Alfa bloker"),
    ("Central alpha-2 Adrenergic Agonist [EPC]", "Santral alfa-2 agonisti"),
    # Lipid
    ("HMG-CoA Reductase Inhibitor [EPC]", "Statin (HMG-KoA redüktaz inhibitörü)"),
    ("Dietary Cholesterol Absorption Inhibitor [EPC]", "Kolesterol emilim inhibitörü"),
    ("PCSK9 Inhibitor [EPC]", "PCSK9 inhibitörü"),
    ("Peroxisome Proliferator Receptor alpha Agonist [EPC]", "Fibrat"),
    # Antiagregan / antikoagülan
    ("P2Y12 Platelet Inhibitor [EPC]", "Antiagregan (P2Y12 inhibitörü)"),
    ("Platelet Aggregation Inhibitor [EPC]", "Antiagregan"),
    ("Factor Xa Inhibitor [EPC]", "Antikoagülan (faktör Xa inhibitörü)"),
    ("Direct Thrombin Inhibitor [EPC]", "Antikoagülan (doğrudan trombin inhibitörü)"),
]

MAX_BRANDS_PER_GENERIC = 15


class CatalogSyncError(Exception):
    """openFDA senkronu tamamlanamadı (ağ yok, oran sınırı, beklenmeyen yanıt). Veritabanı değişmez."""


@dataclass
class SyncReport:
    requests: int = 0
    discovered: int = 0
    created: int = 0
    updated: int = 0
    available: int = 0
    unavailable: list[str] = field(default_factory=list)


class _OpenFDACounter:
    """openFDA `count` uç noktası için küçük istemci: istekler arası bekleme, 429'da sınırlı tekrar."""

    def __init__(self, client: httpx.Client, *, sleep: float, sleeper: Callable[[float], None], report: SyncReport):
        s = get_settings()
        self.client, self.sleep, self.sleeper, self.report = client, sleep, sleeper, report
        self.base_url, self.api_key = s.openfda_base_url, s.openfda_api_key

    def count(self, search: str, field_name: str, limit: int = 1000) -> list[tuple[str, int]]:
        params: dict[str, str | int] = {"search": search, "count": field_name, "limit": limit}
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(3):
            if self.report.requests and self.sleep:
                self.sleeper(self.sleep)
            self.report.requests += 1
            try:
                resp = self.client.get(self.base_url, params=params)
            except httpx.HTTPError as e:
                raise CatalogSyncError(f"openFDA'ya ulaşılamadı ({type(e).__name__}: {e}). "
                                       "İnternet bağlantısını ve OPENFDA_BASE_URL ayarını kontrol edin.") from e
            if resp.status_code == 404:  # openFDA "No matches found!"
                return []
            if resp.status_code == 429 and attempt < 2:
                wait = float(resp.headers.get("Retry-After") or 0) or 2.0 * (attempt + 1)
                log.warning("openFDA oran sınırı; %.1f sn bekleniyor", wait)
                self.sleeper(min(wait, 60.0))
                continue
            if resp.status_code == 429:
                raise CatalogSyncError("openFDA oran sınırı aşıldı (HTTP 429). OPENFDA_API_KEY tanımlayın "
                                       "ya da daha sonra tekrar deneyin.")
            if resp.status_code >= 400:
                raise CatalogSyncError(f"openFDA HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                results = resp.json().get("results") or []
                return [(str(r["term"]), int(r.get("count", 0))) for r in results]
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                raise CatalogSyncError(f"openFDA yanıtı beklenen biçimde değil: {e}") from e
        return []  # pragma: no cover


def _brand_display(brand: str) -> str:
    b = " ".join(brand.split())
    return b.title() if b.isupper() or b.islower() else b


def _is_generic_like(brand: str, generic: str) -> bool:
    """"Metformin Hydrochloride" gibi marka alanına yazılmış etken madde adlarını eler."""
    comps = set(generic.split(" + "))
    bc = set(canonical_components(brand))
    return (bool(bc) and bc <= comps) or normalize(brand) in comps


def _usable_term(generic: str) -> bool:
    comps = generic.split(" + ")
    return bool(generic) and len(comps) <= MAX_COMPONENTS and all(
        2 < len(c) <= 60 and not re.search(r"\d", c) for c in comps)


def sync_from_openfda(
    db: Session,
    *,
    client: httpx.Client | None = None,
    sleep: float = 0.3,
    sleeper: Callable[[float], None] = time.sleep,
    max_generics: int = 300,
    fetch_brands: bool = True,
    classes: list[tuple[str, str]] | None = None,
) -> SyncReport:
    """openFDA'dan etken madde ve ABD marka adlarını çekip katalogu günceller (commit eder).

    Önce kürate liste yüklenir (seed_catalog), sonra:
      1. Her EPC sınıfı için `count=openfda.generic_name.exact` -> etken madde keşfi (sınıf adıyla).
      2. Kürate + keşfedilen her etken madde için `count=openfda.brand_name.exact` -> ABD marka adları ve
         `openfda_available`. Bir marka birden çok etken maddede çıkarsa en çok bileşenliye (kombinasyona)
         bağlanır (metformin sorgusu Janumet'i de döndürür; Janumet sitagliptin + metformin'e aittir).
    Hata olursa hiçbir değişiklik yazılmaz ve CatalogSyncError fırlatılır."""
    report = SyncReport()
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    api = _OpenFDACounter(client, sleep=sleep, sleeper=sleeper, report=report)
    try:
        seed_catalog(db)
        rows = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
        curated = [g for g, r in rows.items() if r.source == SOURCE_TR]

        discovered: dict[str, str] = {}  # generic -> TR sınıf adı
        for epc, tr_label in classes if classes is not None else EPC_CLASSES:
            for term, _count in api.count(f'openfda.pharm_class_epc:"{epc}"', "openfda.generic_name.exact"):
                g = canonical_generic(term)
                if _usable_term(g):
                    discovered.setdefault(g, tr_label)
        report.discovered = len(discovered)

        targets = list(dict.fromkeys(curated + sorted(discovered, key=lambda g: (g.count("+"), g))))[:max_generics]
        brand_owners: dict[str, list[str]] = {}  # normalize(marka) -> etken maddeler
        brand_label: dict[str, str] = {}
        available: set[str] = set(discovered)
        if fetch_brands:
            for g in targets:
                terms = api.count(generic_search_expr(g), "openfda.brand_name.exact", limit=100)
                if terms:
                    available.add(g)
                for term, _ in terms:
                    if _is_generic_like(term, g):
                        continue
                    key = normalize(term)
                    brand_owners.setdefault(key, []).append(g)
                    brand_label.setdefault(key, _brand_display(term))

        us_brands: dict[str, list[str]] = {}
        for key, owners in brand_owners.items():
            top = max(len(o.split(" + ")) for o in owners)
            for o in owners:
                if len(o.split(" + ")) == top:
                    us_brands.setdefault(o, []).append(brand_label[key])

        for g in targets:
            row = rows.get(g)
            is_available = g in available
            brands_us = us_brands.get(g, [])[:MAX_BRANDS_PER_GENERIC]
            if row is None:
                if not is_available:
                    continue
                row = DrugCatalog(generic_name=g, display_name=display_from_generic(g), brand_names=brands_us,
                                  tr_brand_names=[], pharm_class=discovered.get(g), source=SOURCE_OPENFDA,
                                  openfda_available=True)
                _refresh_search_text(row)
                db.add(row)
                rows[g] = row
                report.created += 1
            else:
                merged = _merge_brands(row.tr_brand_names or [], brands_us) if fetch_brands else row.brand_names
                changed = (row.openfda_available, row.brand_names) != (is_available, merged)
                row.openfda_available = is_available
                row.brand_names = merged
                row.pharm_class = row.pharm_class or discovered.get(g)
                row.updated_at = utcnow()
                _refresh_search_text(row)
                report.updated += int(changed)
            if is_available:
                report.available += 1
            elif row.source == SOURCE_TR:
                report.unavailable.append(g)
        db.flush()
        link_unverified_medications(db)
        db.commit()
        return report
    except Exception:
        db.rollback()
        raise
    finally:
        if own_client:
            client.close()

