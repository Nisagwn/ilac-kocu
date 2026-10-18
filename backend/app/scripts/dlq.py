"""Koç işleri dead-letter kuyruğu (DLQ) yönetimi.

Kullanım (backend/ dizininde ya da `docker compose exec worker ...`):
    python -m app.scripts.dlq list [--limit 20]      # DLQ kayıtları (en yeni önce; mesaj içeriği gösterilmez)
    python -m app.scripts.dlq size
    python -m app.scripts.dlq requeue <job_id>       # tek işi yeniden kuyruğa al (deneme sayacı sıfırlanır)
    python -m app.scripts.dlq requeue --all
    python -m app.scripts.dlq purge --yes            # DLQ'yu boşalt, failed işlerdeki mesajları temizle

REDIS_URL gerekir; yoksa 2 koduyla çıkar. Hasta zaten güvenli bir yedek yanıt almıştır; yeniden kuyruğa
alınan iş başarılı olursa sohbete asıl koç yanıtı da eklenir.
"""

from __future__ import annotations

import argparse
import sys

from app.db import SessionLocal
from app.services import jobs


def _redis():
    from app.redis_client import get_redis

    return get_redis()


def main(argv: list[str] | None = None, session_factory=SessionLocal, redis=None) -> int:
    parser = argparse.ArgumentParser(description="Koç işleri dead-letter kuyruğu")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="DLQ kayıtlarını listele")
    p_list.add_argument("--limit", type=int, default=20)
    sub.add_parser("size", help="DLQ boyutu")
    p_req = sub.add_parser("requeue", help="işi yeniden kuyruğa al")
    p_req.add_argument("job_id", nargs="?")
    p_req.add_argument("--all", action="store_true")
    p_purge = sub.add_parser("purge", help="DLQ'yu boşalt")
    p_purge.add_argument("--yes", action="store_true", help="onay")
    args = parser.parse_args(argv)

    r = redis if redis is not None else _redis()
    if r is None:
        print("HATA: REDIS_URL tanımlı değil ya da Redis'e ulaşılamıyor.", file=sys.stderr)
        return 2

    if args.cmd == "size":
        print(jobs.dlq_size(r))
        return 0
    if args.cmd == "list":
        entries = jobs.dlq_entries(r, args.limit)
        if not entries:
            print("DLQ boş.")
        for e in entries:
            print(f"{e.get('failed_at', '-')}  iş={e.get('job_id', e.get('raw'))}  hasta={e.get('patient_id', '-')}  "
                  f"deneme={e.get('attempts', '-')}  hata={e.get('error', '-')}")
        return 0

    db = session_factory()
    try:
        if args.cmd == "requeue":
            if not args.all and not args.job_id:
                print("HATA: job_id ya da --all verin.", file=sys.stderr)
                return 1
            done = jobs.dlq_requeue(db, r, None if args.all else args.job_id)
            print(f"{len(done)} iş yeniden kuyruğa alındı" + (": " + ", ".join(done) if done else "."))
            return 0 if done or args.all else 1
        if args.cmd == "purge":
            if not args.yes:
                print("HATA: DLQ'yu boşaltmak için --yes ile onaylayın.", file=sys.stderr)
                return 1
            print(f"{jobs.dlq_purge(db, r)} kayıt silindi.")
            return 0
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
