"""Integration – chạy sơ bộ bộ câu hỏi vàng (#1–#10, #12, #13) trên seed, Gemini thật.

Embedding VN / reranker theo .env (đang mock khi MEP_MOCK=deepinfra) → chỉ ghi nhận kết quả, không bắt buộc đạt.
Báo cáo: artifacts/query_golden_report.json (không commit).
"""
import json
import re
import time
from pathlib import Path

import pytest
import yaml

from app.services.query.deps import Deps
from app.services.query.graph import build_graph, postgres_checkpointer, run_turn
from tests.fixtures.query_seed.seed import load_seed, write_seed

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "s5_golden.yaml"
REPORT = ROOT / "artifacts" / "query_golden_report.json"
STATUSES = {"OK", "PARTIAL", "NOT_FOUND", "ASK_CLARIFY"}


def norm(text: str) -> str:
    return re.sub(r"[\s·°º]", "", text.lower().replace("²", "2"))


def values_found(answer: str, expected: list) -> list[bool]:
    a = norm(answer)
    return [any(norm(v) in a for v in ([e] if isinstance(e, str) else e)) for e in expected]


def section_hit(sec: dict | None, context: list[dict], doc_ids: dict[str, str]) -> bool | None:
    if not sec:
        return None
    keys = {sec["section_key"], *sec.get("alt_section_keys", []), *sec.get("seed_section_keys", [])}
    doc_id = doc_ids.get(sec["doc_code"])
    return any(b["doc_id"] == doc_id and b["section_key"] in keys for b in context)


def test_golden_preliminary(test_ns):
    if not test_ns.google_api_key or test_ns.is_mock("google_genai"):
        pytest.skip("Thiếu GOOGLE_API_KEY / Gemini đang mock")
    from app.utils.tokens import count_tokens

    data = load_seed(count_tokens=count_tokens)
    write_seed(data, test_ns)
    doc_ids = {d.doc_code: d.doc_id for d in data.documents}
    g = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    deps = Deps.default(test_ns)
    results = []
    with postgres_checkpointer(test_ns) as cp:
        graph = build_graph(deps, cp)
        for q in g["questions"]:
            if not q["in_seed"]:
                continue
            turns = q.get("turns") or [{"question": q["question"], "expected_values": q["expected_values"]}]
            cid, rows = None, []
            for t in turns:
                t0 = time.monotonic()
                out = run_turn(graph, g["user_id"], t["question"], cid)
                cid = out["conversation_id"]
                rows.append({
                    "question": t["question"], "status": out["status"], "seconds": round(time.monotonic() - t0, 1),
                    "values": values_found(out["answer"], t["expected_values"]),
                    "section_in_context": section_hit(q.get("expected_section"), out["context"], doc_ids),
                    "citations": [(c["section_key"], c["page"], c["lang"]) for c in out["citations"]],
                    "active_projects": out["active_projects"], "hop_count": out["hop_count"],
                    "top1": round(out["top1_score"], 3), "answer": out["answer"],
                    "context": [b["section_key"] for b in out["context"]],
                })
                assert out["status"] in STATUSES
            last = rows[-1]
            results.append({
                "id": q["id"], "expected_status": q["expected_status"], "status": last["status"],
                "status_ok": last["status"] == q["expected_status"],
                "values_ok": all(last["values"]) if last["values"] else None,
                "section_in_context": last["section_in_context"], "has_citation": bool(last["citations"]),
                "turns": rows,
            })
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print(f"#{r['id']:>2} status={r['status']:<10} exp={r['expected_status']:<9} values={r['values_ok']} "
              f"section={r['section_in_context']} cite={r['has_citation']} t={[t['seconds'] for t in r['turns']]}")
    assert len(results) == 12
