"""TİTCK ruhsatlı ürün listesinden Türkçe ilaç kataloğunu (app/data/drug_catalog_tr.yaml) üretir.

Kaynak: https://www.titck.gov.tr/dinamikmodul/85 -> "Ruhsatlı Beşeri Tıbbi Ürünler Listesi" (haftalık Excel).
Ham TİTCK dosyası repoya konmaz (boyut/lisans); yalnızca süzülmüş, üretilmiş YAML commit edilir.

Kullanım (backend/ dizininde):
    python -m app.scripts.import_titck ~/Downloads/RuhsatlBeeriTbbirnlerListesi23.10.2026_....xlsx
    python -m app.scripts.import_titck https://titck.gov.tr/storage/Archive/2026/.../...xlsx   # URL de olur
    python -m app.scripts.import_titck liste.xlsx --list-date 23.10.2026 --update-db     # tabloya da yükle
    python -m app.scripts.import_titck liste.csv --output /tmp/katalog.yaml --report       # ayrıntılı rapor

Liste tarihi verilmezse dosya adından okunur ("...Listesi23.10.2026_..."). Kürate katman
(app/data/drug_catalog_curated.yaml) görünen ad, sınıf adı ve takma adları sağlar; kürate markaların TİTCK'da
bulunmayanları raporlanır. `--update-db` üretilen YAML'ı drug_catalog tablosuna upsert eder (seed_catalog).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from app.services import titck
from app.services.drug_catalog import CATALOG_YAML

CURATED_YAML = CATALOG_YAML.parent / "drug_catalog_curated.yaml"


def _download(url: str, dest_dir: Path) -> Path:
    name = url.rsplit("/", 1)[-1].split("?")[0] or "titck.xlsx"
    dest = dest_dir / name
    with httpx.stream("GET", url, timeout=httpx.Timeout(30, read=300), follow_redirects=True,
                      headers={"User-Agent": "ilac-kocu-katalog/1.0"}) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    return dest


def _parse_date(text: str) -> datetime:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"geçersiz tarih: {text!r} (GG.AA.YYYY)")


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description="TİTCK listesinden Türkçe ilaç kataloğu üretimi")
    parser.add_argument("source", help="TİTCK Excel/CSV dosya yolu ya da URL'si")
    parser.add_argument("--list-date", type=_parse_date, help="liste tarihi (GG.AA.YYYY); yoksa dosya adından")
    parser.add_argument("--output", type=Path, default=CATALOG_YAML, help="üretilecek YAML (varsayılan: katalog)")
    parser.add_argument("--curated", type=Path, default=CURATED_YAML, help="kürate katman YAML")
    parser.add_argument("--include-suspended", action="store_true", help="askıdaki ürünleri de dahil et")
    parser.add_argument("--update-db", action="store_true", help="üretilen katalogu veritabanına da yükle")
    parser.add_argument("--report", action="store_true", help="çözülemeyen satırları ve çakışmaları listele")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        src = args.source
        try:
            path = _download(src, Path(tmp)) if src.startswith(("http://", "https://")) else Path(src)
        except httpx.HTTPError as e:
            print(f"HATA: TİTCK dosyası indirilemedi: {e}", file=sys.stderr)
            return 2
        if not path.exists():
            print(f"HATA: dosya bulunamadı: {path}", file=sys.stderr)
            return 2
        try:
            rows, total = titck.read_titck(path)
        except ValueError as e:
            print(f"HATA: {e}", file=sys.stderr)
            return 2
        list_date = args.list_date.date() if args.list_date else titck.list_date_from_name(path.name)

    entries, report = titck.build_catalog(rows, args.curated, include_suspended=args.include_suspended)
    report.total_rows = total
    date_text = list_date.strftime("%d.%m.%Y") if list_date else "tarih bilinmiyor"
    command = "python -m app.scripts.import_titck <TİTCK listesi .xlsx>"
    if args.list_date:
        command += f" --list-date {date_text}"
    if args.include_suspended:
        command += " --include-suspended"
    text = titck.render_yaml(entries, source=f"TİTCK, {date_text}", command=command, report=report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")

    brands = sum(len(e.brands) for e in entries)
    print(f"TİTCK listesi ({date_text}): {total} ürün satırı, ATC filtresinden geçen {report.filtered_rows}")
    print(f"Askıda olduğu için atlanan: {report.suspended_skipped}; etken maddesi çözülen: {report.resolved_rows}; "
          f"çözülemeyen: {sum(report.unresolved.values())}")
    print(f"Katalog: {len(entries)} etken madde / kombinasyon, {brands} TR marka -> {args.output}")
    if report.curated_missing_brands:
        print("TİTCK'da bulunmayan kürate markalar: "
              + ", ".join(f"{b} ({g})" for g, b in report.curated_missing_brands))
    if report.curated_missing_generics:
        print("TİTCK'da ürünü olmayan kürate etken maddeler: " + ", ".join(report.curated_missing_generics))
    if args.report:
        for (atc, ing), n in report.unresolved.most_common():
            print(f"  çözülemedi: {atc} {ing!r} ({n} ürün)")
        for brand, generics in sorted(report.brand_conflicts.items()):
            print(f"  marka çakışması: {brand} -> {', '.join(generics)}")

    if args.update_db:
        from app.db import SessionLocal
        from app.services.drug_catalog import seed_catalog

        db = (session_factory or SessionLocal)()
        try:
            created, updated = seed_catalog(db, args.output)
        finally:
            db.close()
        print(f"Veritabanı: {created} eklendi, {updated} güncellendi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
