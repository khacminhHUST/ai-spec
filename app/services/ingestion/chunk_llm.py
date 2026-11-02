"""LLM chunking: nhận danh sách đơn vị đã đánh số → nhóm liền kề {start, end, topic}. LLM KHÔNG viết lại nội dung.

- Model: config/models.yaml › chunking_llm (temperature 0), qua app.providers.llm.get_chat_model("chunking").
- Kiểm tra output: liên tục, phủ hết, không chồng, mỗi nhóm ≤ max_child_tokens. Sai → fallback gộp tham lam.
- Cache theo hash nội dung (artifacts/_cache/llm/).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import ROOT
from app.prompts import render

log = logging.getLogger(__name__)
CACHE_DIR = ROOT / "artifacts" / "_cache" / "llm"
PROMPT_VERSION = "v1"


@dataclass
class Grouping:
    groups: list[tuple[int, int, str | None]]   # (start, end) 0-based inclusive
    source: str                                  # llm | llm_cache | fallback | single
    reason: str | None = None


def validate_groups(raw, n_units: int, unit_tokens: list[int], max_tokens: int) -> tuple[list[tuple[int, int, str | None]] | None, str | None]:
    """raw: list[{start,end,topic}] (1-based) → (groups 0-based | None, lý do sai)."""
    if not isinstance(raw, list) or not raw:
        return None, "output không phải danh sách"
    groups = []
    expect = 1
    for g in raw:
        if not isinstance(g, dict):
            return None, "phần tử không phải object"
        try:
            s, e = int(g["start"]), int(g["end"])
        except (KeyError, TypeError, ValueError):
            return None, "thiếu start/end"
        if s != expect:
            return None, f"không liên tục/chồng: nhóm bắt đầu {s}, mong đợi {expect}"
        if e < s:
            return None, f"end < start ({s}-{e})"
        if e > n_units:
            return None, f"vượt số đơn vị ({e} > {n_units})"
        tok = sum(unit_tokens[s - 1:e])
        if tok > max_tokens and e > s:
            return None, f"nhóm {s}-{e} vượt token ({tok} > {max_tokens})"
        topic = g.get("topic")
        groups.append((s - 1, e - 1, str(topic).strip()[:120] if topic else None))
        expect = e + 1
    if expect != n_units + 1:
        return None, f"không phủ hết ({expect - 1}/{n_units})"
    return groups, None


def greedy_groups(unit_tokens: list[int], max_tokens: int, min_tokens: int) -> list[tuple[int, int, None]]:
    """Gộp tham lam theo token; nhóm cuối quá nhỏ (< min) → cân bằng lại với nhóm trước."""
    groups: list[list[int]] = []
    cur, tok = [], 0
    for i, t in enumerate(unit_tokens):
        if cur and tok + t > max_tokens:
            groups.append(cur)
            cur, tok = [], 0
        cur.append(i)
        tok += t
    if cur:
        groups.append(cur)
    # nhóm cuối quá nhỏ → chuyển dần đơn vị cuối của nhóm trước sang (không vượt max)
    while len(groups) > 1 and len(groups[-2]) > 1:
        last_tok = sum(unit_tokens[i] for i in groups[-1])
        moved = unit_tokens[groups[-2][-1]]
        if last_tok >= min_tokens or last_tok + moved > max_tokens:
            break
        groups[-1].insert(0, groups[-2].pop())
    return [(g[0], g[-1], None) for g in groups]


def _parse_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < 0:
        raise ValueError("không thấy JSON array")
    return json.loads(text[i:j + 1])


def build_prompt(units: list[str], unit_tokens: list[int], para_start: list[bool], max_tokens: int, min_tokens: int):
    lines = [f"[{i + 1}] ({unit_tokens[i]} tok) {'¶ ' if para_start[i] else ''}{u.strip()}" for i, u in enumerate(units)]
    system = render("ingestion/chunking_system", max_tokens=max_tokens, min_tokens=min_tokens)
    human = render("ingestion/chunking_human", count=len(units), units="\n".join(lines))
    return system, human


def _model_name(llm) -> str:
    return str(getattr(llm, "model", None) or getattr(llm, "model_name", None) or type(llm).__name__)


def group_units(units: list[str], unit_tokens: list[int], para_start: list[bool], llm, max_tokens: int,
                min_tokens: int, use_cache: bool = True) -> Grouping:
    n = len(units)
    if n <= 1:
        return Grouping([(0, max(n - 1, 0), None)], "single")
    system, human = build_prompt(units, unit_tokens, para_start, max_tokens, min_tokens)
    key = hashlib.sha256(f"{PROMPT_VERSION}\n{_model_name(llm)}\n{system}\n{human}".encode()).hexdigest()[:32]
    cache = CACHE_DIR / f"{key}.json"
    raw, source = None, "llm"
    if use_cache and cache.exists():
        try:
            raw, source = json.loads(cache.read_text(encoding="utf-8")), "llm_cache"
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None and llm is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
            content = resp.content if isinstance(resp.content, str) else "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in resp.content)
            raw = _parse_json(content)
            if use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("LLM chunking lỗi: %s", e)
            return Grouping(greedy_groups(unit_tokens, max_tokens, min_tokens), "fallback", f"LLM lỗi: {e}"[:200])
    if raw is None:
        return Grouping(greedy_groups(unit_tokens, max_tokens, min_tokens), "fallback", "không có LLM")
    groups, err = validate_groups(raw, n, unit_tokens, max_tokens)
    if groups is None:
        return Grouping(greedy_groups(unit_tokens, max_tokens, min_tokens), "fallback", err)
    return Grouping(groups, source)


def clear_cache(path: Path = CACHE_DIR) -> None:
    for f in path.glob("*.json"):
        f.unlink()
