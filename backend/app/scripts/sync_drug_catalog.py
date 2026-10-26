"""İlaç kataloğunu openFDA'dan senkronize eder (isteğe bağlı; demo çevrimdışı da çalışır).

Kullanım (backend/ dizininde ya da `docker compose exec api ...`):
    python -m app.scripts.sync_drug_catalog                 # sınıf keşfi + marka adları
    python -m app.scripts.sync_drug_catalog --no-brands     # yalnızca etken madde keşfi (az istek)
    python -m app.scripts.sync_drug_catalog --max-generics 50 --sleep 0.5

Önce kürate Türkçe liste (app/data/drug_catalog_tr.yaml) yüklenir, sonra openFDA `count` uç noktasıyla
diyabet / hipertansiyon / lipid / antiagregan-antikoagülan EPC sınıflarındaki etken maddeler ve ABD marka
adları eklenir. OPENFDA_API_KEY tanımlıysa kullanılır (anahtarsız sınır: dakikada 240, günde 1000 istek).
Geçici 5xx yanıtları tekrarlanır, yine başarısız olan tek sorgu atlanır. Ağ yoksa, oran sınırı aşılırsa ya da
sorguların çoğu başarısızsa veritabanı değişmez ve komut 2 koduyla çıkar. Gerçek API'ye karşı ölçüm: docs/openfda-senkron.md.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.db import SessionLocal
from app.services.drug_catalog import CatalogSyncError, sync_from_openfda


def main(argv: list[str] | None = None, session_factory=SessionLocal) -> int:
    parser = argparse.ArgumentParser(description="openFDA'dan ilaç kataloğu senkronu")
    parser.add_argument("--no-brands", action="store_true", help="ABD marka adlarını çekme (daha az istek)")
    parser.add_argument("--max-generics", type=int, default=300, help="marka sorgusu yapılacak en fazla etken madde")
    parser.add_argument("--sleep", type=float, default=0.3, help="istekler arası bekleme (sn)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = session_factory()
    try:
        report = sync_from_openfda(db, sleep=args.sleep, max_generics=args.max_generics,
                                   fetch_brands=not args.no_brands)
    except CatalogSyncError as e:
        print(f"HATA: openFDA senkronu tamamlanamadı: {e}", file=sys.stderr)
        print("Katalog değiştirilmedi; kürate Türkçe liste (python -m app.seed) çevrimdışı kullanılabilir.",
              file=sys.stderr)
        return 2
    finally:
        db.close()

    print(f"openFDA istekleri: {report.requests}")
    print(f"Sınıflardan keşfedilen etken madde: {report.discovered}")
    print(f"Katalog: {report.created} eklendi, {report.updated} güncellendi; openFDA'da bulunan: {report.available}")
    if report.retries:
        print(f"Tekrar edilen istek (429 / 5xx): {report.retries}")
    if report.failed_queries:
        print(f"Geçici sunucu hatası nedeniyle atlanan sorgu: {len(report.failed_queries)}")
    if report.unavailable:
        print("openFDA'da etiketi bulunamayan kürate etken maddeler: " + ", ".join(sorted(report.unavailable)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
