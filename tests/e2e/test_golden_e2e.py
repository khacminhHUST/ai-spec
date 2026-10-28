"""E2E – bộ câu hỏi vàng trên dữ liệu ingest thật (Giai đoạn 3), chấm theo tiêu chí nghiệm thu docs/query-pipeline.md §6.

Namespace: MEP_E2E_NS (mặc định `ingest`). Báo cáo: artifacts/e2e_report.json.
"""
import json
import os
import statistics
import time
from pathlib import Path

import pytest
import yaml

from query.deps import Deps
from query.graph import build_graph, postgres_checkpointer, stream_turn, turn_config
from shared.db import pg_connect
from tests.query.test_q_golden import values_found

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "s5_golden.yaml"
REPORT = ROOT / "artifacts" / "e2e_report.json"


def run(graph, user_id: str, question: str, cid: str | None) -> dict:
    t0, ttft, cid_out = time.monotonic(), None, None
    for ev in stream_turn(graph, user_id, question, cid):
        if ev["event"] == "token" and ttft is None:
            ttft = time.monotonic() - t0
        if ev["event"] == "done":
            cid_out = ev["data"]["conversation_id"]
    st = graph.get_state(turn_config(cid_out)).values
    return {**st, "conversation_id": cid_out, "ttft": ttft, "seconds": time.monotonic() - t0}


def citation_valid(conn, c: dict) -> bool:
    row = conn.execute(
        "SELECT min(page_start) AS ps, max(page_end) AS pe FROM parent_chunks WHERE doc_id = %s AND section_key = %s",
        (c["doc_id"], c["section_key"]),
    ).fetchone()
    if row["ps"] is None:
        return False
    page = c.get("page")
    return page is None or row["ps"] <= page <= row["pe"]


def test_golden_e2e(settings):
    s = settings.with_ns(os.environ.get("MEP_E2E_NS", "ingest"))
    with pg_connect(s) as conn:
        docs = {r["doc_code"]: r["doc_id"] for r in conn.execute("SELECT doc_code, doc_id FROM documents WHERE is_latest")}
    if not docs:
        pytest.skip(f"namespace {s.pg_schema} chưa có dữ liệu ingest")
    g = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    deps = Deps.default(s)
    results = []
    with postgres_checkpointer(s) as cp, pg_connect(s) as conn:
        graph = build_graph(deps, cp)
        for q in g["questions"]:
            turns = q.get("turns") or [{"question": q["question"], "expected_values": q["expected_values"]}]
            cid, rows = None, []
            for t in turns:
                out = run(graph, g["user_id"], t["question"], cid)
                cid = out["conversation_id"]
                rows.append({
                    "question": t["question"], "status": out["status"], "answer": out["answer"],
                    "ttft": out["ttft"] and round(out["ttft"], 2), "seconds": round(out["seconds"], 2),
                    "values": values_found(out["answer"], t["expected_values"]),
                    "context": [(b["doc_id"], b["section_key"]) for b in out.get("context") or []],
                    "citations": out.get("citations") or [],
                    "citations_valid": [citation_valid(conn, c) for c in out.get("citations") or []],
                    "active_projects": out.get("active_projects"), "top1": out.get("top1_score"),
                    "hop_count": out.get("hop_count"),
                })
            last, sec = rows[-1], q.get("expected_section")
            keys = sec and {sec["section_key"], *sec.get("alt_section_keys", [])}
            r = {
                "id": q["id"], "expected_status": q["expected_status"], "status": last["status"],
                "section_hit": None if not sec else any(d == docs.get(sec["doc_code"]) and k in keys for d, k in last["context"]),
                "values_ok": all(last["values"]) if last["values"] else None,
                "citation_ok": None if last["status"] in ("NOT_FOUND", "ASK_CLARIFY") else
                               bool(last["citations"]) and all(last["citations_valid"]),
                "turns": rows,
            }
            if q.get("expected_citation_section"):
                r["citation_section_ok"] = any(c["section_key"] == q["expected_citation_section"] for c in last["citations"])
            if q.get("expected_active_projects"):
                r["projects_ok"] = all(t["active_projects"] == q["expected_active_projects"] for t in rows)
            results.append(r)

    def rate(key):
        xs = [r[key] for r in results if r.get(key) is not None]
        return sum(xs) / len(xs) if xs else None

    ttfts = [t["ttft"] for r in results for t in r["turns"] if t["ttft"] is not None]
    q2 = next(r for r in results if r["id"] == 2)
    summary = {
        "retrieval_hit_rate": rate("section_hit"),
        "values_rate": rate("values_ok"),
        "citation_valid_rate": rate("citation_ok"),
        "q12_not_found": next(r for r in results if r["id"] == 12)["status"] == "NOT_FOUND",
        "q2_has_en": any("en" in (c["lang"] if isinstance(c["lang"], list) else [c["lang"]])
                         for c in q2["turns"][-1]["citations"]),
        "q13_projects_ok": next(r for r in results if r["id"] == 13).get("projects_ok"),
        "q11_cites_sec_9_7": next(r for r in results if r["id"] == 11).get("citation_section_ok"),
        "ttft_p50": round(statistics.median(ttfts), 2) if ttfts else None,
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print(f"#{r['id']:>2} {r['status']:<10} section={r['section_hit']} values={r['values_ok']} "
              f"cite={r['citation_ok']} ttft={[t['ttft'] for t in r['turns']]}")
    print(json.dumps(summary, ensure_ascii=False))

    assert summary["retrieval_hit_rate"] >= 0.9
    assert summary["values_rate"] >= 0.85
    assert summary["citation_valid_rate"] == 1.0
    assert summary["q12_not_found"] and summary["q2_has_en"] and summary["q13_projects_ok"]
    assert summary["ttft_p50"] is not None and summary["ttft_p50"] < 10.0
