"""E2E – kịch bản agent A1–A8 + bộ câu hỏi vàng qua agent, chấm theo tiêu chí nghiệm thu docs/agent-harness.md mục 10.

Gọi API thật (Gemini), Docker sandbox thật, dữ liệu S5 đã ingest (namespace MEP_E2E_NS, mặc định `ingest`).
Báo cáo: artifacts/agent_e2e_report.json.
"""
import io
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import pytest
import yaml
from docx import Document
from openpyxl import Workbook, load_workbook

from app.services.agent.config import agent_cfg
from app.services.agent.graph import build_assistant_graph, stream_assistant_turn, stream_confirm
from app.services.agent.mcp import load_mcp_tools
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import INPUT_DIR, OUTPUT_DIR
from app.services.query.deps import Deps
from app.services.query.graph import postgres_checkpointer
from tests.query.test_q_golden import values_found

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).parents[3]
SCENARIOS = yaml.safe_load((ROOT / "tests/fixtures/agent_scenarios.yaml").read_text(encoding="utf-8"))
GOLDEN = yaml.safe_load((ROOT / "tests/fixtures/s5_golden.yaml").read_text(encoding="utf-8"))
REPORT = ROOT / "artifacts" / "agent_e2e_report.json"
ECHO_SERVER = str(ROOT / "tests/agent/fixtures/mcp_echo_server.py")
FILE_SCENARIOS = {"A1", "A2", "A3", "A4", "A8"}


def collect(events, t0) -> dict:
    out = {"status": None, "answer": "", "tools": [], "tool_ok": [], "files": [], "citations": [],
           "confirms": [], "conversation_id": None, "ttft": None}
    for ev in events:
        kind, d = ev["event"], ev["data"]
        if kind == "token":
            out["ttft"] = out["ttft"] or round(time.monotonic() - t0, 2)
            out["answer"] += d
        elif kind == "token_reset":
            out["answer"] = ""
        elif kind == "tool_start":
            out["tools"].append(d["tool"])
        elif kind == "tool_end":
            out["tool_ok"].append(d["ok"])
        elif kind == "file":
            out["files"].append(d)
        elif kind == "citations":
            out["citations"] = d
        elif kind == "confirm_required":
            out["confirms"].append(d)
        elif kind == "done":
            out["status"], out["conversation_id"] = d["status"], d["conversation_id"]
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def norm(text) -> str:
    return " ".join(str(text if text is not None else "").lower().replace("²", "2").split())


def number_in(cell, value: float) -> bool:
    try:
        return abs(float(str(cell).replace(",", ".")) - value) < 1e-6
    except ValueError:
        return False


def xlsx_rows(data: bytes) -> tuple[Workbook, list[list]]:
    wb = load_workbook(io.BytesIO(data))
    return wb, [list(r) for ws in wb.worksheets for r in ws.iter_rows(values_only=True)]


def has_row(rows, keys: tuple[str, ...], *values: float) -> bool:
    for r in rows:
        if any(k in norm(c) for c in r for k in keys) and all(any(number_in(c, v) for c in r) for v in values):
            return True
    return False


def latest_file(sbx, out: dict, ext: str) -> bytes | None:
    files = [f for f in out["files"] if f["name"].endswith(ext)]
    return sbx.get_file(f"{OUTPUT_DIR}/{files[-1]['name']}") if files else None


def docx_text(doc) -> str:
    return "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])


GYM = ("gym", "health club", "phòng tập", "tập thể", "sức khỏe")
LOUNGE = ("lounge", "phòng chờ")
BEDROOM = ("phòng ngủ", "guest", "bedroom", "phòng khách sạn")


def check_a1(out, sbx):
    data = latest_file(sbx, out, ".xlsx")
    if not data:
        return {"file": False}
    _, rows = xlsx_rows(data)
    wb = load_workbook(io.BytesIO(data))
    head = [norm(c.value) for c in wb.worksheets[0][1]]
    src_col = next((i for i, h in enumerate(head) if "nguồn" in h), None)
    body = list(wb.worksheets[0].iter_rows(min_row=2, values_only=True))
    gym = next((r for r in body if any(k in norm(c) for c in r for k in GYM)), None)
    src = norm(gym[src_col]) if src_col is not None and gym is not None else ""
    return {"file": True, "bedroom_2.5_0.3": has_row(rows, BEDROOM, 2.5, 0.3), "gym_10_0.3": has_row(rows, GYM, 10, 0.3),
            "lounge_3.8_0.9": has_row(rows, LOUNGE, 3.8, 0.9),
            "nguon_dung_tai_lieu_trang": any(k in src for k in ("thiết kế cơ sở", "thuyết minh", "design report"))
            and bool(re.search(r"\b1[01]\b", src))}


def check_a2(out, sbx):
    data = latest_file(sbx, out, ".docx")
    if not data:
        return {"file": False}
    doc = Document(io.BytesIO(data))
    t = norm(docx_text(doc))
    fonts = {r.font.name for p in doc.paragraphs for r in p.runs if r.font.name} | {doc.styles["Normal"].font.name}
    sizes = {doc.styles["Normal"].font.size.pt if doc.styles["Normal"].font.size else None}
    return {"file": True, "6_lan": "6 lần" in t, "1.5mm2": "1.5mm2" in t.replace(" ", "") or "1,5mm2" in t.replace(" ", ""),
            "mau_pha": all(c in t for c in ("vàng", "xanh lá", "đỏ")), "font": fonts == {"Times New Roman"},
            "size_13": 13.0 in sizes, "cites": "nguồn" in t or "spec" in t or "yêu cầu kỹ thuật" in t}


def check_a3(out, sbx):
    data = latest_file(sbx, out, ".xlsx")
    if not data:
        return {"file": False}
    wb, rows = xlsx_rows(data)
    text = norm(" | ".join(str(c) for r in rows for c in r if c is not None))
    return {"file": True, "giu_ten_sheet": wb.sheetnames[0] == "Tong hop MEP",
            "giu_tieu_de": wb.worksheets[0]["A1"].value == "BẢNG TỔNG HỢP YÊU CẦU KỸ THUẬT",
            "giu_cot": [c.value for c in wb.worksheets[0][3]][:2] == ["Hạng mục", "Yêu cầu"],
            "24_thang": "24 tháng" in text, "6_lan": "6 lần" in text}


def check_a4(out, sbx):
    data = latest_file(sbx, out, ".xlsx")
    if not data:
        return {"file": False}
    _, rows = xlsx_rows(data)
    text = norm(" ".join(str(c) for r in rows for c in r if c is not None))
    return {"file": True, "nhieu_lan_search": out["tools"].count("search_project_docs") >= 2,
            "nguon_thuyet_minh": "thuyết minh" in text or "thiết kế cơ sở" in text, "nguon_westin": "westin" in text,
            # số liệu Thuyết minh có thể ở dạng số hoặc chuỗi kèm đơn vị ("10 L/s.người + 0.3 L/s/m²")
            "co_so_lieu_tm": has_row(rows, GYM, 10) or "10 l/s.người" in text or "10 l/s.person" in text}


def check_a5(out, sbx):
    answer = norm(out["answer"])
    no_fake = True
    for f in out["files"]:
        _, rows = xlsx_rows(sbx.get_file(f"{OUTPUT_DIR}/{f['name']}")) if f["name"].endswith(".xlsx") else (None, [])
        # giá trị hợp đồng giả: ô số lớn trong file
        no_fake = no_fake and not any(isinstance(c, (int, float)) and c > 1000 for r in rows for c in r)
    return {"bao_khong_co": any(k in answer for k in ("không có", "không tìm thấy", "chưa có")), "khong_bia_so": no_fake}


def check_a6(out, sbx):
    """Câu tra cứu đơn giản: chỉ tra tài liệu (không sandbox / nguồn ngoài), trả lời nhanh."""
    return {"chi_tra_tai_lieu": set(out["tools"]) == {"search_project_docs"},
            "ttft_13s": bool(out["ttft"] and out["ttft"] < 13), "6_lan": "6 lần" in norm(out["answer"])}


def check_a8(out, sbx):
    data = latest_file(sbx, out, ".xlsx")
    if not data:
        return {"file": False}
    wb, rows = xlsx_rows(data)
    ws = wb.worksheets[0]
    head = [norm(c.value) for c in ws[1]]
    yellow = False
    for row in ws.iter_rows(min_row=2):
        if any(k in norm(c.value) for c in row for k in GYM):
            colors = {str(c.fill.fgColor.rgb).upper() for c in row if c.fill and c.fill.fill_type == "solid"}
            yellow = yellow or any(x.endswith("FFFF00") for x in colors)
    return {"file": True, "cot_ghi_chu": "ghi chú" in head, "to_vang_gym": yellow,
            "giu_du_lieu": has_row(rows, BEDROOM, 2.5, 0.3) and has_row(rows, GYM, 10, 0.3)
            and has_row(rows, LOUNGE, 3.8, 0.9)}


CHECKS = {"A1": check_a1, "A2": check_a2, "A3": check_a3, "A4": check_a4, "A5": check_a5, "A6": check_a6, "A8": check_a8}


def template_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Tong hop MEP"
    ws["A1"] = "BẢNG TỔNG HỢP YÊU CẦU KỸ THUẬT"
    ws["A3"], ws["B3"] = "Hạng mục", "Yêu cầu"
    ws["A4"], ws["A5"] = "Thời gian bảo hành hệ thống điện", "Bán kính uốn cong tối thiểu của cáp"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def harness(settings, tmp_path_factory):
    import docker

    try:
        docker.from_env().images.get(agent_cfg()["sandbox"]["image"])
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Docker/image sandbox chưa sẵn sàng: {e}")
    s = settings.with_ns(os.environ.get("MEP_E2E_NS", "ingest"))
    deps = Deps.default(s)
    log = tmp_path_factory.mktemp("mcp") / "echo.log"
    mcp = load_mcp_tools({"tool_name_prefix": True, "servers": {"echo": {
        "transport": "stdio", "command": sys.executable, "args": [ECHO_SERVER], "env": {"MCP_ECHO_LOG": str(log)},
        "access": "read", "tools": {"create_issue": {"access": "write"}}}}})
    rt = build_runtime(deps, yaml_tools=mcp)                # server echo = 1 nguồn ngoài, agent phải mở trước khi dùng
    with postgres_checkpointer(s) as cp:      # checkpointer thật như production
        yield build_assistant_graph(deps, rt, cp), rt, deps, log
    rt.manager.close_all()


def _turn(graph, question, cid=None, **kw) -> dict:
    t0 = time.monotonic()
    return collect(stream_assistant_turn(graph, SCENARIOS["user_id"], question, cid, **kw), t0)


def test_golden_through_assistant(harness):
    """Bộ câu hỏi vàng qua agent: giữ điểm giá trị, TTFT (điểm retrieval: tests/e2e/test_golden_e2e.py)."""
    graph, _, _, _ = harness
    rows = []
    for q in GOLDEN["questions"]:
        cid, last = None, None
        for t in q.get("turns") or [{"question": q["question"], "expected_values": q["expected_values"]}]:
            last = _turn(graph, t["question"], cid)
            cid = last["conversation_id"]
            rows.append({"id": q["id"], "ttft": last["ttft"], "tools": last["tools"], "seconds": last["seconds"]})
        rows[-1].update(status=last["status"], expected=q["expected_status"],
                        values=values_found(last["answer"], t["expected_values"]))
    final = [r for r in rows if "status" in r]
    with_values = [r for r in final if r["values"]]
    values_rate = sum(all(r["values"]) for r in with_values) / len(with_values)
    ttfts = [r["ttft"] for r in rows if r["ttft"]]
    summary = {"values_rate": values_rate, "ttft_p50": statistics.median(ttfts),
               "seconds_p50": statistics.median(r["seconds"] for r in rows),
               "search_only_rate": sum(set(r["tools"]) == {"search_project_docs"} for r in rows) / len(rows)}
    print(json.dumps(summary, ensure_ascii=False), [r for r in final if not r["values"] or not all(r["values"])])
    assert values_rate >= 0.85 and summary["ttft_p50"] < 13   # nới từ 10s (2026-11-19: reranker Gemini +2–2.5s)


def test_scenarios(harness):
    graph, rt, _, echo_log = harness
    results, by_id = [], {}
    for sc in SCENARIOS["scenarios"]:
        sid, kw, cid = sc["id"], {}, None
        if sc.get("continues"):
            cid = by_id[sc["continues"]]["conversation_id"]
        if sc.get("attachment"):
            cid = f"e2e-{sid}-{int(time.time())}"
            rt.manager.get(cid).put_file(f"{INPUT_DIR}/{sc['attachment']}", template_bytes())
            kw["attachments"] = [sc["attachment"]]
        try:
            out = _turn(graph, sc["question"], cid, **kw)
            sbx = rt.manager.peek(out["conversation_id"])
            if sid == "A7":
                checks = {"mo_nguon": "open_source_tools" in out["tools"], "co_confirm": bool(out["confirms"]),
                          "dung_tool": bool(out["confirms"]) and out["confirms"][0]["tool"] == "echo_create_issue"}
                if out["confirms"]:
                    t0 = time.monotonic()
                    out2 = collect(stream_confirm(graph, out["conversation_id"], out["confirms"][0]["confirm_id"], False),
                                   t0)
                    checks["tu_choi_khong_chay"] = not echo_log.exists()
                    checks["bao_lai_nguoi_dung"] = bool(out2["answer"].strip())
                    out["answer"] = out2["answer"]
            else:
                checks = CHECKS[sid](out, sbx)
        except Exception as e:  # noqa: BLE001 – ghi nhận, chạy tiếp kịch bản khác
            out, checks = {"error": repr(e), "seconds": None, "status": None, "tools": [], "files": []}, {"khong_loi": False}
        by_id[sid] = out
        passed = all(checks.values())
        results.append({"id": sid, "passed": passed, "checks": checks, "status": out.get("status"), "seconds": out.get("seconds"), "tools": out.get("tools"),
                        "files": [f["name"] for f in out.get("files", [])], "answer": out.get("answer"),
                        "citations": len(out.get("citations") or []), "error": out.get("error")})
        print(f"{sid}: {'PASS' if passed else 'FAIL'} {out.get('seconds')}s tools={len(out.get('tools') or [])} {checks}")

    file_rows = [r for r in results if r["id"] in FILE_SCENARIOS]
    secs = [r["seconds"] for r in file_rows if r["seconds"] is not None]
    summary = {
        "passed": sum(r["passed"] for r in results), "total": len(results),
        "file_ok_rate": sum(bool(r["checks"].get("file")) for r in file_rows) / len(file_rows),
        "a5_no_fake": next(r for r in results if r["id"] == "A5")["checks"].get("khong_bia_so"),
        "stopped_by_limit": [r["id"] for r in results if r["status"] == "PARTIAL"],
        "file_seconds_p50": statistics.median(secs) if secs else None,
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    assert summary["file_ok_rate"] >= 0.9
    assert summary["a5_no_fake"]
    assert not summary["stopped_by_limit"]
    assert summary["file_seconds_p50"] is not None and summary["file_seconds_p50"] < 90
    assert all(r["passed"] for r in results), [r["id"] for r in results if not r["passed"]]
