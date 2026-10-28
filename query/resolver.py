"""N2. Project Resolver – code (không LLM).

mention → chuẩn hóa (normalize_alias) → khớp chính xác project_aliases → fuzzy (rapidfuzz ≥ fuzzy_threshold),
chỉ trong dự án user có quyền → áp set/add/remove/keep → nạp project_profiles (cache TTL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from shared.keys import normalize_alias

from query.deps import Deps


@dataclass
class Resolution:
    active_projects: list[str]
    resolved: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)              # mention không khớp dự án nào (có quyền)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)   # mention → nhiều project_id
    auto_selected: bool = False

    @property
    def needs_clarify(self) -> bool:
        return bool(self.unmatched or self.ambiguous or not self.active_projects)


def match_mention(mention: str, alias_rows: list[dict[str, Any]], threshold: float) -> list[str]:
    """→ danh sách project_id khớp (rỗng = không khớp; > 1 = mơ hồ)."""
    norm = normalize_alias(mention)
    if not norm:
        return []
    exact = list(dict.fromkeys(r["project_id"] for r in alias_rows if r["alias_norm"] == norm))
    if exact:
        return exact
    best: dict[str, float] = {}
    for r in alias_rows:
        score = fuzz.ratio(norm, r["alias_norm"])
        if score >= threshold:
            best[r["project_id"]] = max(best.get(r["project_id"], 0.0), score)
    if not best:
        return []
    top = max(best.values())
    return [pid for pid, s in best.items() if s == top]


def apply_action(action: str, active: list[str], resolved: list[str]) -> list[str]:
    if action == "set":
        return list(resolved) if resolved else list(active)
    if action == "add":
        return list(dict.fromkeys([*active, *resolved]))
    if action == "remove":
        return [p for p in active if p not in resolved]
    # keep: giữ nguyên; nếu chưa có dự án mà câu hỏi có nhắc → dùng dự án được nhắc
    return list(active) if active else list(resolved)


def resolve(deps: Deps, user_id: str, action: str, mentions: list[str], active: list[str]) -> Resolution:
    allowed = deps.repo.user_projects(user_id)
    rows = deps.repo.aliases(allowed)
    threshold = float(deps.cfg("project").get("fuzzy_threshold", 85))
    resolved, unmatched, ambiguous = [], [], {}
    for m in mentions:
        pids = [p for p in match_mention(m, rows, threshold) if p in allowed]
        if not pids:
            unmatched.append(m)
        elif len(pids) > 1:
            ambiguous[m] = pids
        else:
            resolved.append(pids[0])
    resolved = list(dict.fromkeys(resolved))
    active = [p for p in active if p in allowed]  # quyền có thể đã đổi
    if unmatched or ambiguous:
        return Resolution(active, resolved, unmatched, ambiguous)
    new_active = apply_action(action, active, resolved)
    res = Resolution(new_active, resolved)
    if not new_active and len(allowed) == 1:
        # Giả định: user chỉ có quyền 1 dự án → không cần hỏi lại, dùng luôn dự án đó.
        res.active_projects, res.auto_selected = list(allowed), True
    return res


# ------------------------------------------------------------------ profile

def build_profile(deps: Deps, project_id: str) -> dict[str, Any]:
    """Ngôn ngữ theo stream (suy từ `documents` is_latest), thứ tự ưu tiên nguồn, ngôn ngữ ưu tiên hợp đồng."""
    info = deps.repo.project_info([project_id]).get(project_id, {})
    streams: dict[str, dict[str, Any]] = {}
    docs: dict[str, dict[str, Any]] = {}
    for d in deps.repo.documents([project_id]):
        if not d.get("stream"):
            continue
        st = streams.setdefault(d["stream"], {"languages": [], "doc_ids": []})
        st["doc_ids"].append(d["doc_id"])
        for lang in d.get("languages") or []:
            if lang not in st["languages"]:
                st["languages"].append(lang)
        docs[d["doc_id"]] = {"title": d.get("title"), "revision": d.get("revision"), "stream": d["stream"],
                             "doc_code": d.get("doc_code"), "languages": list(d.get("languages") or [])}
    priority = list(info.get("source_priority") or [])
    return {
        "project_id": project_id,
        "name": info.get("name", project_id),
        "aliases": list(info.get("aliases") or []),
        "streams": streams,
        "docs": docs,
        "source_priority": priority,
        "governing_language": info.get("governing_language", "vi"),
    }


def load_profiles(deps: Deps, project_ids: list[str]) -> dict[str, dict[str, Any]]:
    return {pid: deps.cached_profile(pid, lambda pid=pid: build_profile(deps, pid)) for pid in project_ids}


def stream_priority(profile: dict[str, Any], stream: str | None) -> int:
    pr = profile.get("source_priority") or []
    return pr.index(stream) + 1 if stream in pr else len(pr) + 1


def clarify_message(deps: Deps, user_id: str, res: Resolution) -> tuple[str, list[dict[str, Any]]]:
    """Câu hỏi lại + danh sách lựa chọn (tên dự án, không lộ ID nội bộ ngoài project_id)."""
    allowed = deps.repo.user_projects(user_id)
    info = deps.repo.project_info(allowed)
    options_ids = sorted({p for ps in res.ambiguous.values() for p in ps}) or allowed
    options = [{"project_id": p, "name": info.get(p, {}).get("name", p), "aliases": info.get(p, {}).get("aliases", [])}
               for p in options_ids]
    lines = []
    if res.unmatched:
        lines.append(f"Không tìm thấy dự án/công trình khớp với: {', '.join(res.unmatched)} "
                     "(hoặc bạn chưa có quyền truy cập).")
    for m, pids in res.ambiguous.items():
        lines.append(f"\"{m}\" khớp với nhiều dự án: {', '.join(info.get(p, {}).get('name', p) for p in pids)}.")
    if not res.unmatched and not res.ambiguous:
        lines.append("Bạn muốn hỏi về dự án/công trình nào?")
    if options:
        lines.append("Vui lòng chọn một trong các dự án sau:")
        lines += [f"- {o['name']}" + (f" ({', '.join(o['aliases'])})" if o["aliases"] else "") for o in options]
    else:
        lines.append("Bạn chưa được cấp quyền truy cập dự án nào.")
    return "\n".join(lines), options
