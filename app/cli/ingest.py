"""CLI: python -m app.cli.ingest <file|dir> [--project 26DA06 --building S5] [--dry-run] [--force] [--ns ingest]

- Thư mục → ingest mọi *.pdf bên trong (đệ quy).
- --dry-run: chỉ sinh artifact (artifacts/<doc_id>/), không embed, không ghi DB.
- --force  : file đã ingest (cùng file_hash) → xoá bản ghi cũ và ghi lại (mặc định: bỏ qua).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.core.config import get_settings
from app.services.ingestion.pipeline import ingest_file, iter_inputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli.ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="file PDF hoặc thư mục")
    ap.add_argument("--project", help="project_id (khi không suy ra được từ đường dẫn/bìa)")
    ap.add_argument("--building", help="công trình (VD S5)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ns", help="namespace (mặc định MEP_NS)")
    ap.add_argument("--no-cache", action="store_true", help="không dùng cache parse/LLM")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    if args.ns is not None:
        s = s.with_ns(args.ns)
    files = iter_inputs(args.target)
    if not files:
        print("Không có file PDF nào", file=sys.stderr)
        return 1
    rc = 0
    for f in files:
        try:
            res = ingest_file(f, project=args.project, building=args.building, dry_run=args.dry_run,
                              force=args.force, settings=s, use_cache=not args.no_cache)
        except Exception as e:  # noqa: BLE001
            logging.exception("Lỗi ingest %s", f)
            res, rc = {"path": str(f), "status": "error", "error": str(e)}, 2
        print(json.dumps(res, ensure_ascii=False, default=str))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
