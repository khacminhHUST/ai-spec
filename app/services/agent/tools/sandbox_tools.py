"""Tool sandbox: run_code, read_file, write_file, list_files.

conversation_id lấy từ AgentContext (runtime), không phải tham số LLM → mỗi conversation chỉ thấy sandbox của mình.
"""
from __future__ import annotations

import itertools
import posixpath
from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.services.agent.sandbox import OUTPUT_DIR, WORKSPACE, SandboxError, SandboxManager, workspace_path
from app.services.agent.sandbox.base import RUN_DIR
from app.services.agent.state import runtime_context
from app.services.agent.tools.base import clip, fail, ok

_run_seq = itertools.count(1)

RUN_CODE_DESC = """Chạy 1 đoạn Python 3.11 trong sandbox riêng của cuộc hội thoại (không có mạng, không có DB/API).
Dùng khi: tạo/sửa file (xlsx, docx, pdf, pptx, csv, png), đọc file nhị phân (xlsx/docx người dùng gửi), xử lý số liệu.
Không dùng để tra cứu tài liệu dự án (dùng search_project_docs) và không tự điền số liệu từ kiến thức chung.
- Thư mục: /workspace/input (file người dùng gửi, kết quả tra cứu lớn *.json), /workspace/output (file kết quả – chỉ file ở đây mới gửi được cho người dùng).
- Có sẵn: openpyxl, xlsxwriter, python-docx, python-pptx, reportlab, pandas, matplotlib, mep_helpers (helper công ty), LibreOffice (`soffice --headless`).
- In kết quả cần xem bằng print(); stdout/stderr bị cắt ngắn.
- timeout_s: mặc định theo cấu hình, tối đa có giới hạn.
Ví dụ: run_code(code="import pandas as pd\\ndf = pd.DataFrame({'a': [1]})\\ndf.to_excel('/workspace/output/a.xlsx', index=False)\\nprint('ok')")
Trả về JSON: ok, exit_code, stdout, stderr, timed_out, new_files (file mới/đổi trong /workspace/output)."""

READ_FILE_DESC = """Đọc file TEXT trong /workspace (utf-8: .txt, .json, .csv, .md, .py…). Đường dẫn tương đối tính từ /workspace.
File dài → đọc tiếp bằng offset (vị trí ký tự, xem next_offset). File nhị phân (xlsx, docx, pdf) → dùng run_code để đọc.
Ví dụ: read_file(path="input/search_1.json"), read_file(path="input/search_1.json", offset=16000)"""

WRITE_FILE_DESC = """Ghi file TEXT (utf-8) vào /workspace, ghi đè nếu đã có. Đường dẫn tương đối tính từ /workspace.
Dùng cho script, csv, md, json. File Office (xlsx/docx/pdf) → tạo bằng run_code.
Ví dụ: write_file(path="output/ghi_chu.md", content="# Ghi chú\\n…")"""

LIST_FILES_DESC = """Liệt kê file (đệ quy) trong 1 thư mục của /workspace, kèm kích thước (bytes).
Ví dụ: list_files(dir="input"), list_files(dir="output"), list_files() → toàn bộ /workspace"""


def _snapshot(sbx, d: str) -> dict[str, tuple[int, float]]:
    return {f.path: (f.size, f.mtime) for f in sbx.list(d)}


def _rel(path: str) -> str:
    return posixpath.relpath(path, WORKSPACE)


def run_code_impl(manager: SandboxManager, cfg: dict[str, Any], conversation_id: str, code: str,
                  timeout_s: int | None = None) -> str:
    try:
        sbx = manager.get(conversation_id)
        t = min(int(timeout_s or cfg["exec_timeout_s"]), int(cfg["max_exec_timeout_s"]))
        before = _snapshot(sbx, OUTPUT_DIR)
        script = sbx.put_file(f"{RUN_DIR}/run_{next(_run_seq)}.py", code.encode("utf-8"))
        res = sbx.exec(["python", script], timeout_s=t)
        after = _snapshot(sbx, OUTPUT_DIR)
    except SandboxError as e:
        return fail(str(e))
    max_bytes = int(cfg["max_output_file_mb"]) << 20
    new_files = [{"path": _rel(p), "size": s, **({"warning": "vượt giới hạn tải về"} if s > max_bytes else {})}
                 for p, (s, m) in sorted(after.items()) if before.get(p) != (s, m)]
    n = int(cfg["output_max_chars"])
    payload = dict(exit_code=res.exit_code, stdout=clip(res.stdout, n), stderr=clip(res.stderr, n),
                   timed_out=res.timed_out, new_files=new_files)
    if res.timed_out:
        return fail(f"Quá thời gian {t}s – tối ưu code hoặc chia nhỏ", **payload)
    if res.exit_code != 0:
        return fail("Code lỗi (xem stderr)", **payload)
    return ok(**payload)


def read_file_impl(manager: SandboxManager, cfg: dict[str, Any], conversation_id: str, path: str,
                   offset: int = 0) -> str:
    try:
        p = workspace_path(path)
        data = manager.get(conversation_id).get_file(p, max_bytes=int(cfg["max_output_file_mb"]) << 20)
    except SandboxError as e:
        return fail(str(e))
    try:
        text = data.decode("utf-8")
        if "\x00" in text:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL")
    except UnicodeDecodeError:
        return fail(f"File nhị phân ({len(data)} bytes) – dùng run_code để đọc", path=_rel(p))
    n = int(cfg["read_file_max_chars"])
    offset = max(0, int(offset or 0))
    chunk = text[offset:offset + n]
    end = offset + len(chunk)
    return ok(path=_rel(p), total_chars=len(text), offset=offset, content=chunk,
              next_offset=end if end < len(text) else None)


def write_file_impl(manager: SandboxManager, conversation_id: str, path: str, content: str) -> str:
    try:
        p = manager.get(conversation_id).put_file(path, content.encode("utf-8"))
    except SandboxError as e:
        return fail(str(e))
    return ok(path=_rel(p), size=len(content.encode("utf-8")))


def list_files_impl(manager: SandboxManager, conversation_id: str, dir: str = WORKSPACE) -> str:
    try:
        files = manager.get(conversation_id).list(workspace_path(dir or WORKSPACE))
    except SandboxError as e:
        return fail(str(e))
    return ok(dir=_rel(workspace_path(dir or WORKSPACE)), files=[{"path": _rel(f.path), "size": f.size} for f in files])


def make_sandbox_tools(manager: SandboxManager, cfg: dict[str, Any]) -> list[BaseTool]:
    """cfg = agent.yaml › sandbox."""

    @tool("run_code", description=RUN_CODE_DESC)
    def run_code(code: str, runtime: ToolRuntime, timeout_s: int | None = None) -> str:
        return run_code_impl(manager, cfg, runtime_context(runtime).conversation_id, code, timeout_s)

    @tool("read_file", description=READ_FILE_DESC)
    def read_file(path: str, runtime: ToolRuntime, offset: int = 0) -> str:
        return read_file_impl(manager, cfg, runtime_context(runtime).conversation_id, path, offset)

    @tool("write_file", description=WRITE_FILE_DESC)
    def write_file(path: str, content: str, runtime: ToolRuntime) -> str:
        return write_file_impl(manager, runtime_context(runtime).conversation_id, path, content)

    @tool("list_files", description=LIST_FILES_DESC)
    def list_files(runtime: ToolRuntime, dir: str = WORKSPACE) -> str:
        return list_files_impl(manager, runtime_context(runtime).conversation_id, dir)

    return [run_code, read_file, write_file, list_files]
