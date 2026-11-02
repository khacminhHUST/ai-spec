"""Unit – skills: đọc frontmatter, liệt kê đúng, load_skill trả nội dung."""
import json

import pytest

from app.services.agent.skills import load_skills, skills_prompt
from app.services.agent.skills.loader import parse_skill_md
from app.services.agent.tools.skill_tool import make_skill_tool

pytestmark = pytest.mark.unit


@pytest.fixture
def skills_dir(tmp_path):
    (tmp_path / "b-skill").mkdir()
    (tmp_path / "b-skill" / "SKILL.md").write_text(
        "---\nname: b-skill\ndescription: >\n  Xuất file B\n  nhiều dòng\n---\n# B\nBước 1", encoding="utf-8")
    (tmp_path / "b-skill" / "scripts").mkdir()
    (tmp_path / "b-skill" / "scripts" / "x.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "a-skill").mkdir()
    (tmp_path / "a-skill" / "SKILL.md").write_text("﻿---\nname: a-skill\ndescription: Skill A\n---\nNội dung A",
                                                   encoding="utf-8")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("không có frontmatter", encoding="utf-8")
    return tmp_path


def test_parse_frontmatter():
    meta, body = parse_skill_md("---\nname: x\ndescription: y\n---\nbody")
    assert meta == {"name": "x", "description": "y"} and body == "body"
    assert parse_skill_md("plain") == ({}, "plain")


def test_list_skills(skills_dir):
    skills = load_skills(skills_dir)
    assert list(skills) == ["a-skill", "b-skill"]                    # broken bị bỏ qua, sắp theo tên
    assert skills["b-skill"].description == "Xuất file B nhiều dòng"
    assert skills["b-skill"].files == ["scripts/x.py"]
    assert skills_prompt(skills) == "- a-skill: Skill A\n- b-skill: Xuất file B nhiều dòng"


def test_load_skill_tool(skills_dir):
    t = make_skill_tool(load_skills(skills_dir))
    out = json.loads(t.invoke({"name": "b-skill"}))
    assert out["ok"] and out["content"] == "# B\nBước 1" and "---" not in out["content"]
    miss = json.loads(t.invoke({"name": "zzz"}))
    assert not miss["ok"] and miss["available"] == ["a-skill", "b-skill"]
