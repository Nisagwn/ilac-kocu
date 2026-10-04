"""Eğitim içeriği embedding'lerini yeniden üretir.

Kullanım:
    python -m app.services.rag.reindex          # eksik / farklı modelle üretilmiş embedding'ler
    python -m app.services.rag.reindex --all    # tümü (ör. embedding algoritması değiştiyse)
"""

import argparse

from app.db import SessionLocal
from app.services.rag.embeddings import get_provider
from app.services.rag.search import ensure_embeddings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eğitim içeriği embedding'lerini yeniden üretir")
    parser.add_argument("--all", action="store_true", help="tüm satırları yeniden embed et")
    args = parser.parse_args(argv)
    provider = get_provider()
    with SessionLocal() as db:
        n = ensure_embeddings(db, provider, force=args.all)
        db.commit()
    print(f"{n} içerik embed edildi (model: {provider.model_id}).")
    return n


if __name__ == "__main__":
    main()
