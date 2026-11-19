"""Worker kho tri thức: python -m app.cli.worker [--ns ingest] [--once] [-v]

Xử lý hàng chờ ingest_jobs (upload qua giao diện): parse → nháp (DRAFT), phát hành → DB tìm kiếm.
Mỗi lần 1 job. Khởi động: job dở dang của lần chạy trước → FAILED (bấm Chạy lại trên giao diện).
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.core.config import get_settings
from app.db.client import pg_connect
from app.services.knowledge.worker import recover_interrupted, run_forever, run_once


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli.worker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", help="namespace (mặc định MEP_NS)")
    ap.add_argument("--once", action="store_true", help="xử lý hết hàng chờ rồi thoát")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("app.services.knowledge").setLevel(logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    if args.ns is not None:
        s = s.with_ns(args.ns)
    if args.once:
        with pg_connect(s, autocommit=True) as conn:
            recover_interrupted(conn)
        while (job_id := run_once(s)) is not None:
            print(job_id)
        return 0
    try:
        run_forever(s, hard_cancel=True)   # tiến trình riêng: hủy job đang chạy → thoát, Docker bật lại
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
