"""openFDA ilaç etiketinden eğitim içeriği üretimi (kind='openfda').

openFDA metinleri İngilizcedir ve ABD etiketine aittir; burada Türkçe çerçeve içinde kısaltılmış
alıntılar olarak saklanır. Doz içeren cümleler (ör. "500 mg") güvenlik için çıkarılır; doz bölümü
(dosage_and_administration) hiç alınmaz.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EducationContent, set_review
from app.services import openfda
from app.services.rag.embeddings import document_text, get_provider

log = logging.getLogger(__name__)

DISCLAIMER = "Bu bilgi ABD FDA etiketinden alınmıştır, eğitim amaçlıdır; ilacınızla ilgili kararları hekiminizle verin."
SOURCE_NAME = "openFDA Drug Label API (ABD FDA etiketi)"
FIELD_CHARS = 350

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DOSE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|mL|units?|IU)\b", re.IGNORECASE)
_SECTION_NO = re.compile(r"^\s*\d+(?:\.\d+)*\s+[A-Z &]+\s+")  # "1 INDICATIONS AND USAGE " başlığı


def _summarize(text: str | None, limit: int = FIELD_CHARS) -> str | None:
    if not text:
        return None
    text = _SECTION_NO.sub("", text.replace("…", "").strip())
    out = ""
    for sent in _SENTENCE_SPLIT.split(text):
        sent = sent.strip()
        if not sent or _DOSE_RE.search(sent):
            continue
        if len(out) + len(sent) + 1 > limit:
            if not out:  # tek uzun cümle: kelime sınırında kes
                out = sent[:limit].rsplit(" ", 1)[0] + "…"
            else:
                out += " …"
            break
        out = f"{out} {sent}".strip()
    return out or None


def build_body(data: dict) -> str | None:
    ingredients = ", ".join(data.get("active_ingredients") or data.get("generic_names") or []) or data.get(
        "resolved_generic", "")
    parts = [
        ("Kullanım amacı (etiketten)", _summarize(data.get("indications"))),
        ("Önemli uyarılar", _summarize(data.get("warnings"))),
        ("Etkileşimler", _summarize(data.get("drug_interactions"))),
    ]
    if not any(text for _, text in parts):
        return None
    lines = [f"Etken madde: {ingredients}."]
    lines += [f"{label}: {text}" for label, text in parts if text]
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def source_url_for(data: dict) -> str:
    base = "https://api.fda.gov/drug/label.json?search="
    if data.get("set_id"):
        return base + quote(f"set_id:{data['set_id']}")
    return base + quote(f'openfda.generic_name:"{data.get("resolved_generic", "")}"')


def ingest_label_data(db: Session, data: dict) -> EducationContent | None:
    """parse_label çıktısını drug_key'e göre upsert eder ve embed eder (commit eder).

    Yeni ya da metni değişen içerik hekim onayı bekler (review_status="pending"); reddedilmiş ve metni
    değişmemiş içerik reddedilmiş kalır. İçerik çıkarılamazsa None döner."""
    drug_key = (data.get("resolved_generic") or "").strip().lower()
    body = build_body(data)
    if not drug_key or not body:
        return None
    title = f"{drug_key.capitalize()} — ilaç etiketi bilgisi (openFDA)"
    tags = f"ilac,openfda,{drug_key}"

    row = db.scalar(select(EducationContent).where(EducationContent.drug_key == drug_key))
    if row is None:
        row = EducationContent(drug_key=drug_key, kind="openfda")
        set_review(row, "pending")
        db.add(row)
    changed = (row.title, row.body, row.tags) != (title, body, tags)
    if changed and row.id is not None:
        set_review(row, "pending")  # etiket metni değişti: hekim yeniden onaylamalı
    row.title, row.body, row.tags = title, body, tags
    row.source_name, row.source_url = SOURCE_NAME, source_url_for(data)
    if changed or row.embedding_model is None:
        provider = get_provider()
        try:
            row.embedding = provider.embed_documents([document_text(title, body, tags)])[0]
            row.embedding_model = provider.model_id
        except Exception:  # harici sağlayıcı hatası: ilk aramada tembel olarak yeniden embed edilir
            log.warning("openFDA içeriği embed edilemedi (%s)", drug_key, exc_info=True)
            row.embedding, row.embedding_model = None, None
    db.commit()
    return row


def ingest_drug_label(db: Session, name: str) -> EducationContent | None:
    """İlaç adından (TR ticari isim veya etken madde) openFDA etiketini çekip eğitim içeriğine ekler.

    Aynı etken madde için idempotenttir. openfda.DrugLabelNotFound / OpenFDAUnavailable fırlatabilir."""
    return ingest_label_data(db, openfda.get_label_cached(name))
