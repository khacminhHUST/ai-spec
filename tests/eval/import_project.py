"""Nạp file export 1 dự án từ prod (chunk + vector, xem CLAUDE.md › Test) vào namespace local (mặc định `prod`). Chạy lại được (xóa dự án đó trước khi nạp).

  python -m app.cli.migrate --ns prod
  python -m tests.eval.import_project prod_data/ct05.jsonl.gz [--ns prod]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, ".")

ORDER = ["projects", "project_aliases", "project_buildings", "documents", "parent_chunks"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ns", default="prod")
    a = ap.parse_args()
    os.environ["MEP_NS"] = a.ns
    from psycopg.types.json import Jsonb
    from qdrant_client import models as qm

    from app.core.config import get_settings
    from app.db.client import pg_connect, qdrant

    s = get_settings()
    assert s.ns == a.ns
    rows: dict[str, list[dict]] = {t: [] for t in ORDER}
    points: dict[str, list] = {"vi": [], "en": []}
    pid = None
    with gzip.open(a.file, "rt", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o["t"] == "meta":
                pid = o["project_id"]
            elif o["t"] == "point":
                v = o["vector"]
                if isinstance(v, dict) and isinstance(v.get("sparse"), dict):
                    v = {**v, "sparse": qm.SparseVector(**v["sparse"])}
                points[o["lang"]].append(qm.PointStruct(id=o["id"], vector=v, payload=o["payload"]))
            else:
                rows[o["t"]].append(o["row"])
    pg = pg_connect(s, autocommit=True)
    pg.execute("DELETE FROM documents WHERE project_id=%s", (pid,))
    for t in ("project_aliases", "project_buildings", "projects"):
        pg.execute(f"DELETE FROM {t} WHERE project_id=%s", (pid,))
    with pg.cursor() as cur:
        for t in ORDER:
            if not rows[t]:
                continue
            cols = list(rows[t][0])
            vals = [[Jsonb(r[c]) if isinstance(r[c], dict) else r[c] for c in cols] for r in rows[t]]
            cur.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", vals)
    q = qdrant(s)
    flt = qm.Filter(must=[qm.FieldCondition(key="project_id", match=qm.MatchValue(value=pid))])
    for lang, pts in points.items():
        coll = s.collection(lang)
        q.delete(coll, qm.FilterSelector(filter=flt), wait=True)
        for i in range(0, len(pts), 128):
            q.upsert(coll, pts[i:i + 128], wait=True)
    print(pid, {t: len(r) for t, r in rows.items()}, {k: len(v) for k, v in points.items()})


if __name__ == "__main__":
    main()
