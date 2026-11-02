"""Skills: skills/<name>/SKILL.md (frontmatter YAML: name, description) + file đi kèm (nếu có)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.core.config import ROOT

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path                     # thư mục skill
    body: str                      # nội dung SKILL.md bỏ frontmatter
    files: list[str] = field(default_factory=list)   # file đi kèm (tương đối thư mục skill)


def parse_skill_md(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return (meta if isinstance(meta, dict) else {}), m.group(2)


def skills_root(skills_dir: str | Path) -> Path:
    p = Path(skills_dir)
    return p if p.is_absolute() else ROOT / p


def load_skills(skills_dir: str | Path) -> dict[str, Skill]:
    """→ {name: Skill}, sắp theo tên. Thiếu name/description → bỏ qua."""
    root = skills_root(skills_dir)
    out: dict[str, Skill] = {}
    for md in sorted(root.glob("*/SKILL.md")):
        meta, body = parse_skill_md(md.read_text(encoding="utf-8"))
        name, desc = str(meta.get("name") or "").strip(), str(meta.get("description") or "").strip()
        if not name or not desc:
            continue
        files = sorted(str(f.relative_to(md.parent)).replace("\\", "/") for f in md.parent.rglob("*")
                       if f.is_file() and f.name != "SKILL.md")
        out[name] = Skill(name, " ".join(desc.split()), md.parent, body.strip(), files)
    return dict(sorted(out.items()))


def skills_prompt(skills: dict[str, Skill]) -> str:
    """Danh sách name + description cho system prompt (nội dung nạp bằng load_skill khi cần)."""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills.values()) or "(không có skill)"
