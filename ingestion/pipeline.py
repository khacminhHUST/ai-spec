"""Điều phối 6 bước Ingestion cho 1 file + artifact debug (artifacts/<doc_id>/)."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingestion import docling_cache
from ingestion.bilingual import detect_languages, postprocess, split_languages
from ingestion.chunks import (ParentDraft, boundary_violations, build_children, build_parents, integrity_check,
                              md_row, split_large_sections, table_header_md)
from ingestion.metadata import DocMeta, infer_metadata, make_doc_id, meta_summary
from ingestion.parse import ParsedDoc, TextLayer, parse_pdf
from ingestion.structure import Structure, build_structure, recover_textlayer_headings, reorder_headings, toc_chapters
from ingestion.textutil import chunk_cfg, normalize_text, profile
from shared.config import ROOT, Settings, get_settings
from shared.schema import ChildChunk, Document, ParentChunk
from shared.tokens import count_tokens

log = logging.getLogger(__name__)
ARTIFACTS = ROOT / "artifacts"


@dataclass
class LangResult:
    lang: str
    structure: Structure
    drafts: list[ParentDraft]
    children: list[ChildChunk]
    llm_stats: dict


@dataclass
class IngestResult:
    meta: DocMeta
    document: Document
    parents: list[ParentChunk]
    children: list[ChildChunk]
    report: dict[str, Any]
    per_lang: dict[str, LangResult] = field(default_factory=dict)
    parsed: ParsedDoc | None = None


# ---------------------------------------------------------------- xử lý

def process(path: str | Path, *, project: str | None = None, building: str | None = None, llm=None,
            settings: Settings | None = None, use_cache: bool = True, workers: int = 8) -> IngestResult:
    """Bước 1–5 (không ghi DB)."""
    s = settings or get_settings()
    t0 = time.time()
    path = Path(path)
    file_hash = docling_cache.file_sha256(path)
    timings: dict[str, float] = {}

    parsed = parse_pdf(path, file_hash, use_cache=use_cache)
    timings["parse"] = round(time.time() - t0, 1)
    meta = infer_metadata(path, file_hash, parsed.blocks, project=project, building=building, settings=s)
    prof = profile(meta.doc_type)
    warnings: list[str] = list(meta.warnings)

    # bước 3: ngôn ngữ / song ngữ
    t = time.time()
    langinfo = detect_languages(parsed, prof)
    per_lang_blocks = split_languages(parsed, prof, langinfo, warnings)
    layer = TextLayer(path)
    chapters = None
    if prof.get("chapters_from_toc"):
        c = prof["chapters_from_toc"]
        chapters = toc_chapters({p: layer.page_lines(p) for p in range(1, min(c["max_page"], parsed.n_pages) + 1)}, c,
                                parsed.n_pages)
    timings["bilingual"] = round(time.time() - t, 1)

    # bước 4–5
    if llm is None:
        from shared.llm import get_chat_model

        llm = get_chat_model("chunking", s)
    per_lang: dict[str, LangResult] = {}
    for lang, blocks in per_lang_blocks.items():
        t = time.time()
        blocks = reorder_headings(postprocess(blocks, prof, warnings))
        if not langinfo["bilingual"]:
            n_rec = recover_textlayer_headings(blocks, layer, prof, parsed.n_pages, lang)
            if n_rec:
                warnings.append(f"{lang}: khôi phục {n_rec} heading từ text layer (Docling bỏ sót)")
        st = build_structure(blocks, prof, chapters)
        warnings += [f"{lang}: {w}" for w in st.warnings]
        split_large_sections(st, prof, lang, warnings)
        drafts = build_parents(st, meta, lang)
        children, stats = build_children(drafts, meta, lang, llm, workers=workers, use_cache=use_cache)
        warnings += [f"{lang}: {w}" for w in stats.pop("warnings")]
        per_lang[lang] = LangResult(lang, st, drafts, children, stats)
        timings[f"chunk_{lang}"] = round(time.time() - t, 1)

    langs = sorted(per_lang, key=lambda x: (x != langinfo["primary"], x))
    document = Document(
        doc_id=meta.doc_id, doc_code=meta.doc_code, project_id=meta.project_id, building=meta.building,
        stream=meta.stream, disciplines=meta.disciplines, title=meta.title, issuer=meta.issuer,
        revision=meta.revision, issue_date=meta.issue_date, stage=meta.stage, languages=langs,
        is_bilingual=langinfo["bilingual"], is_latest=True, source_path=meta.source_path, file_hash=file_hash,
        warnings=warnings,
    )
    parents = [d.parent for r in per_lang.values() for d in r.drafts]
    children = [c for r in per_lang.values() for c in r.children]
    report = make_report(parsed, meta, langinfo, per_lang, warnings, layer)
    report["timings"] = {**timings, "total": round(time.time() - t0, 1)}
    return IngestResult(meta, document, parents, children, report, per_lang, parsed)


# ---------------------------------------------------------------- kiểm tra + báo cáo

def _alnum(s: str) -> str:
    return re.sub(r"[\W_]+", "", normalize_text(s).lower())


def page_check(children: list[ChildChunk], layer: TextLayer, n: int = 20, seed: int = 0) -> dict:
    """Kiểm tra ngẫu nhiên: đoạn đầu raw_text của child có trên các trang page_start..page_end của PDF gốc."""
    rnd = random.Random(seed)
    sample = rnd.sample(children, min(n, len(children)))
    ok, bad = 0, []
    cache: dict[int, str] = {}
    for c in sample:
        # 8 từ đầu (≥ 3 ký tự) phải có trên trang (so từng từ: trang song ngữ xen dòng EN/VN trong text layer)
        words = [_alnum(w) for w in re.split(r"[\s|]+", c.raw_text)]
        words = [w for w in words if len(w) >= 3][:8]
        pages = range(c.page_start or 1, (c.page_end or c.page_start or 1) + 1)
        text = "".join(cache.setdefault(p, _alnum(" ".join(layer.page_lines(p)))) for p in pages)
        hit = sum(1 for w in words if w in text)
        if words and hit >= max(1, len(words) - 1):
            ok += 1
        else:
            bad.append(f"{c.child_id} tr.{c.page_start}-{c.page_end}: {c.raw_text[:50]!r}")
    return {"sampled": len(sample), "ok": ok, "bad": bad}


def make_report(parsed: ParsedDoc, meta: DocMeta, langinfo: dict, per_lang: dict[str, LangResult],
                warnings: list[str], layer: TextLayer) -> dict:
    max_t = chunk_cfg()["max_child_tokens"]
    rep: dict[str, Any] = {"doc_id": meta.doc_id, "doc_code": meta.doc_code, "metadata": meta_summary(meta),
                           "parse": parsed.info, "language": langinfo, "langs": {}}
    for lang, r in per_lang.items():
        by_parent: dict[str, list[ChildChunk]] = {}
        for c in r.children:
            by_parent.setdefault(c.parent_id, []).append(c)
        fails = [d.parent.section_key for d in r.drafts if not integrity_check(d, by_parent.get(d.parent.parent_id, []))]
        over = []
        for c in r.children:
            tk = count_tokens(c.text, lang)
            if tk > max_t:
                single_row = c.type == "TABLE_ROWS" and c.raw_text.count("\n") == 0
                over.append({"child_id": c.child_id, "tokens": tk, "single_table_row": single_row})
        empty = [c.child_id for c in r.children if not c.raw_text.strip()]
        rep["langs"][lang] = {
            "n_sections": sum(1 for d in r.drafts if d.parent.type == "SECTION"),
            "n_tables": sum(1 for d in r.drafts if d.parent.type == "TABLE"),
            "n_parents": len(r.drafts),
            "n_children": len(r.children),
            "n_children_text": sum(1 for c in r.children if c.type == "TEXT"),
            "n_children_table": sum(1 for c in r.children if c.type == "TABLE_ROWS"),
            "integrity_pass": len(r.drafts) - len(fails), "integrity_fail": fails,
            "oversized_children": over, "empty_children": empty,
            "boundary_violations": boundary_violations(r.children),
            "llm": r.llm_stats,
            "rejected_headings": len(r.structure.rejected),
            "section_keys": [d.parent.section_key for d in r.drafts],
        }
    if len(per_lang) == 2:
        a, b = (rep["langs"][x]["section_keys"] for x in per_lang)
        la, lb = list(per_lang)
        rep["section_diff"] = {f"only_{la}": [k for k in a if k not in set(b)],
                               f"only_{lb}": [k for k in b if k not in set(a)]}
    allc = [c for r in per_lang.values() for c in r.children]
    rep["page_check"] = page_check(allc, layer)
    rep["n_warnings"] = len(warnings)
    rep["warnings"] = warnings
    return rep


# ---------------------------------------------------------------- artifact

def render_md(r: LangResult) -> str:
    tables: dict[str, list[ParentDraft]] = {}
    for d in r.drafts:
        if d.parent.type == "TABLE":
            tables.setdefault(d.parent.parent_section_key or "", []).append(d)
    out = []
    for d in r.drafts:
        if d.parent.type != "SECTION":
            continue
        out.append(d.parent.content_md)
        for t in tables.get(d.parent.section_key, []):
            out.append(f"<!-- {t.parent.section_key} tr.{t.parent.page_start}-{t.parent.page_end} -->\n"
                       + t.parent.content_md)
    return "\n\n".join(out) + "\n"


def write_artifacts(res: IngestResult, out_root: Path = ARTIFACTS) -> Path:
    d = out_root / res.meta.doc_id
    d.mkdir(parents=True, exist_ok=True)
    parsed = {"info": res.parsed.info if res.parsed else {}, "n_pages": res.parsed.n_pages if res.parsed else None,
              "blocks": [b.to_json() for b in (res.parsed.blocks if res.parsed else [])],
              "removed_header_footer": [b.to_json() for b in (res.parsed.removed if res.parsed else [])]}
    (d / "parsed.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for lang, r in res.per_lang.items():
        (d / f"{lang}.md").write_text(render_md(r), encoding="utf-8")
    with (d / "parents.jsonl").open("w", encoding="utf-8") as f:
        for p in res.parents:
            f.write(p.model_dump_json() + "\n")
    with (d / "children.jsonl").open("w", encoding="utf-8") as f:
        for c in res.children:
            f.write(c.model_dump_json() + "\n")
    (d / "report.json").write_text(json.dumps(res.report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return d


# ---------------------------------------------------------------- API

def ingest_file(path: str | Path, *, project: str | None = None, building: str | None = None,
                dry_run: bool = False, force: bool = False, settings: Settings | None = None, llm=None,
                use_cache: bool = True, out_root: Path = ARTIFACTS) -> dict:
    """Ingest 1 file → tóm tắt (status, doc_id, số parent/child, artifact)."""
    from ingestion import store

    s = settings or get_settings()
    path = Path(path)
    file_hash = docling_cache.file_sha256(path)
    if not dry_run and not force:
        from shared.db import pg_connect

        with pg_connect(s) as conn:
            if store.find_by_hash(conn, file_hash):
                return {"status": "skipped", "reason": "file_hash đã ingest (dùng --force để ghi lại)",
                        "doc_id": make_doc_id(file_hash), "path": str(path)}
    res = process(path, project=project, building=building, llm=llm, settings=s, use_cache=use_cache)
    art = write_artifacts(res, out_root)
    summary = {
        "path": str(path), "doc_id": res.meta.doc_id, "doc_code": res.meta.doc_code,
        "parents": {k: v["n_parents"] for k, v in res.report["langs"].items()},
        "children": {k: v["n_children"] for k, v in res.report["langs"].items()},
        "integrity_fail": {k: len(v["integrity_fail"]) for k, v in res.report["langs"].items()},
        "n_warnings": res.report["n_warnings"], "artifacts": str(art), "timings": res.report["timings"],
    }
    if dry_run:
        return {"status": "dry_run", **summary}
    saved = store.save_document(res.document, res.parents, res.children, s, force=force)
    res.report["store"] = saved
    (art / "report.json").write_text(json.dumps(res.report, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")
    return {**summary, **saved}


def iter_inputs(target: str | Path) -> list[Path]:
    p = Path(target)
    if p.is_dir():
        return sorted(x for x in p.rglob("*") if x.suffix.lower() == ".pdf")
    return [p]
