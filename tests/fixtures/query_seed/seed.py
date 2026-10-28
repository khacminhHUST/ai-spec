"""Seed dữ liệu giả lập cho Query Pipeline (phiên B).

Dữ liệu viết tay trong *.yaml cùng thư mục (nội dung chép nguyên văn từ bộ S5). Module này dựng
Document / ParentChunk / ChildChunk theo đúng schema `shared.schema` (validate Pydantic) và nạp vào
Postgres + Qdrant của 1 namespace.

    python -m tests.fixtures.query_seed.seed              # namespace theo MEP_NS
    python -m tests.fixtures.query_seed.seed --ns test_x  # namespace khác (migrate trước)

Embedding: get_embedder(lang) → thật nếu có key & không mock, ngược lại MockEmbedder.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from shared.config import Settings, get_settings
from shared.keys import extract_references, make_child_id, make_parent_id
from shared.schema import ChildChunk, Document, ParentChunk

SEED_DIR = Path(__file__).parent
SEED_FILES = ("spec_dien.yaml", "thuyet_minh.yaml", "chi_dan_pccc.yaml")


def _approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 3)) if text else 0


@dataclass
class SeedData:
    documents: list[Document] = field(default_factory=list)
    parents: list[ParentChunk] = field(default_factory=list)
    children: list[ChildChunk] = field(default_factory=list)

    def extend(self, other: "SeedData") -> "SeedData":
        self.documents += other.documents
        self.parents += other.parents
        self.children += other.children
        return self


def child_breadcrumb(doc: Document, number: str | None, title: str | None, topic: str | None) -> str:
    """`[Dự án | Stream | Discipline | Mục | Topic]` (docs/ingestion.md mục 5)."""
    project = " ".join(x for x in (doc.project_id, doc.building) if x)
    sec = " ".join(x for x in (number, title) if x)
    return f"[{project} | {doc.stream} | {', '.join(doc.disciplines)} | {sec} | {topic or ''}]"


def build_document(spec: dict[str, Any], count_tokens: Callable[[str], int] = _approx_tokens) -> SeedData:
    """1 file yaml → SeedData (đã validate)."""
    doc = Document(**spec["document"])
    label = spec.get("doc_label") or doc.title or doc.doc_id
    out = SeedData(documents=[doc])
    for p in spec["parents"]:
        for lang in ("vi", "en"):
            part = p.get(lang)
            if not part:
                continue
            title = part.get("title")
            kids = part["children"]
            content = part.get("content_md") or "\n".join(c["raw_text"] for c in kids)
            pid = make_parent_id(doc.doc_id, lang, p["section_key"])
            pages = [pg for c in kids for pg in c["pages"]]
            out.parents.append(ParentChunk(
                parent_id=pid, doc_id=doc.doc_id, section_key=p["section_key"],
                parent_section_key=p.get("parent_section_key"), lang=lang, type=p["type"],
                number=p.get("number"), title=title,
                breadcrumb=" > ".join(x for x in (label, " ".join(y for y in (p.get("number"), title) if y)) if x),
                content_md=content, page_start=min(pages), page_end=max(pages),
                referenced_ids=extract_references(content), token_count=count_tokens(content),
            ))
            for seq, c in enumerate(kids, start=1):
                ctype = c.get("type") or ("TABLE_ROWS" if p["type"] == "TABLE" else "TEXT")
                bc = child_breadcrumb(doc, p.get("number"), title, c.get("topic"))
                out.children.append(ChildChunk(
                    child_id=make_child_id(pid, seq), parent_id=pid, section_key=p["section_key"], lang=lang,
                    type=ctype, text=f"{bc}\n{c['raw_text']}", raw_text=c["raw_text"], topic=c.get("topic"),
                    page_start=c["pages"][0], page_end=c["pages"][1],
                    project_id=doc.project_id, building=doc.building, stream=doc.stream,
                    disciplines=doc.disciplines, doc_id=doc.doc_id, revision=doc.revision, is_latest=doc.is_latest,
                ))
    return out


def load_seed(files: tuple[str, ...] = SEED_FILES, count_tokens: Callable[[str], int] = _approx_tokens) -> SeedData:
    data = SeedData()
    for name in files:
        with (SEED_DIR / name).open(encoding="utf-8") as f:
            data.extend(build_document(yaml.safe_load(f), count_tokens))
    return data


# ------------------------------------------------------------------ nạp DB

def write_seed(data: SeedData, settings: Settings, *, embed: bool = True) -> dict[str, int]:
    """Upsert vào Postgres (documents, parent_chunks) + Qdrant (child_chunks_<lang>)."""
    from qdrant_client import models as qm

    from shared.db import pg_connect, qdrant
    from shared.embedding import get_embedder
    from shared.sparse import doc_sparse

    doc_cols = list(Document.model_fields)
    par_cols = list(ParentChunk.model_fields)
    with pg_connect(settings, autocommit=True) as conn, conn.transaction():
        for d in data.documents:
            row = d.model_dump()
            conn.execute(
                f"INSERT INTO documents ({', '.join(doc_cols)}) VALUES ({', '.join(['%s'] * len(doc_cols))}) "
                f"ON CONFLICT (doc_id) DO UPDATE SET {', '.join(f'{c}=EXCLUDED.{c}' for c in doc_cols if c != 'doc_id')}",
                [row[c] for c in doc_cols],
            )
        for p in data.parents:
            row = p.model_dump()
            conn.execute(
                f"INSERT INTO parent_chunks ({', '.join(par_cols)}) VALUES ({', '.join(['%s'] * len(par_cols))}) "
                f"ON CONFLICT (parent_id) DO UPDATE SET {', '.join(f'{c}=EXCLUDED.{c}' for c in par_cols if c != 'parent_id')}",
                [row[c] for c in par_cols],
            )
    client = qdrant(settings)
    n_points = 0
    for lang in ("vi", "en"):
        kids = [c for c in data.children if c.lang == lang]
        if not kids:
            continue
        dim = int(settings.embedding_cfg(lang)["dimensions"])
        if embed:
            vecs = get_embedder(lang, settings).embed_documents([c.text for c in kids])
        else:
            vecs = [[1.0] + [0.0] * (dim - 1) for _ in kids]
        points = [
            qm.PointStruct(
                id=c.point_id,
                vector={"dense": v, "sparse": qm.SparseVector(**doc_sparse(c.text))},
                payload=c.qdrant_payload(),
            )
            for c, v in zip(kids, vecs)
        ]
        client.upsert(settings.collection(lang), points=points, wait=True)
        n_points += len(points)
    return {"documents": len(data.documents), "parents": len(data.parents), "children": n_points}


def add_aliases(settings: Settings, rows: list[tuple[str, str, str, str, str]]) -> None:
    """Thêm alias (alias_norm, alias, project_id, building, kind) – dùng cho dự án giả trong test."""
    from shared.db import pg_connect

    with pg_connect(settings, autocommit=True) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO project_aliases (alias_norm, alias, project_id, building, kind) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING", rows,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", default=None)
    ap.add_argument("--migrate", action="store_true", help="chạy shared.migrate trước")
    args = ap.parse_args()
    s = get_settings()
    if args.ns is not None:
        s = s.with_ns(args.ns)
    if args.migrate:
        from shared.migrate import migrate

        migrate(s)
    from shared.tokens import count_tokens

    print(write_seed(load_seed(count_tokens=count_tokens), s))


if __name__ == "__main__":
    main()
