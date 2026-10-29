"""Quản lý ngữ cảnh agent: kết quả tool lớn → ghi file JSON trong sandbox, trả LLM bản tóm tắt + đường dẫn."""
from __future__ import annotations

import itertools
import json
from typing import Any, Callable

from app.services.agent.sandbox import INPUT_DIR, SandboxManager

_seq = itertools.count(1)


def approx_tokens(text: str) -> int:
    """Ước lượng nhanh (≈ 3 ký tự/token cho VN/EN lẫn) khi không cần chính xác."""
    return max(1, len(text) // 3) if text else 0


def offload_json(manager: SandboxManager, conversation_id: str, prefix: str, payload: Any) -> str:
    """Ghi payload đầy đủ vào /workspace/input/<prefix>_<n>.json. → đường dẫn tương đối /workspace."""
    name = f"{prefix}_{next(_seq)}.json"
    data = json.dumps(payload, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    manager.get(conversation_id).put_file(f"{INPUT_DIR}/{name}", data)
    return f"input/{name}"


def fit_or_offload(manager: SandboxManager, conversation_id: str, prefix: str, payload: dict[str, Any],
                   summarize: Callable[[dict[str, Any]], dict[str, Any]], max_tokens: int,
                   count_tokens: Callable[[str], int] = approx_tokens) -> tuple[dict[str, Any], str | None]:
    """payload ≤ max_tokens → trả nguyên; lớn hơn → ghi file + trả summarize(payload) kèm `full_result_file`."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if count_tokens(text) <= max_tokens:
        return payload, None
    path = offload_json(manager, conversation_id, prefix, payload)
    short = summarize(payload)
    short["full_result_file"] = path
    short["note"] = (f"Kết quả đầy đủ quá dài nên đã ghi vào /workspace/{path}. Đọc bằng read_file (đọc tiếp bằng "
                     f"offset) hoặc json.load trong run_code để lấy đủ số liệu.")
    return short, path
