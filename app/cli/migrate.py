"""Migration: bảng Postgres + collection Qdrant + checkpoint LangGraph + seed project_aliases.

    python -m app.cli.migrate                  # namespace theo MEP_NS
    python -m app.cli.migrate --ns test_x      # namespace khác
    python -m app.cli.migrate --recreate       # xóa & tạo lại collection Qdrant (đổi model/dim)
    python -m app.cli.migrate --drop --ns x    # xóa schema + collection của namespace
"""
from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.db.migrate import drop, migrate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", default=None)
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if args.ns is not None:
        s = s.with_ns(args.ns)
    if args.drop:
        drop(s)
        print(f"dropped namespace {s.pg_schema}")
        return
    print(migrate(s, args.recreate))


if __name__ == "__main__":
    main()
