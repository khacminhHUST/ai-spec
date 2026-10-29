"""Prompt LLM dạng file text: app/prompts/<service>/<tên>.md.

Placeholder `{tên}` được thay bằng render(); dấu ngoặc nhọn khác (VD JSON mẫu) giữ nguyên.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
_VAR_RE = re.compile(r"\{(\w+)\}")


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """'query/router_system' → nội dung app/prompts/query/router_system.md (bỏ xuống dòng cuối file)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


def render(name: str, **values: object) -> str:
    """Thay {key} một lượt (giá trị chèn vào không bị thay tiếp)."""
    return _VAR_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), load(name))
