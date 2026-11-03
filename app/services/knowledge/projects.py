"""Dự án / công trình (bảng projects, project_buildings). Mỗi lần sửa → sinh lại project_aliases của dự án."""
from __future__ import annotations

import re
from typing import Any

from app.db.migrate import project_alias_rows, replace_aliases
from app.models.document import Stream
from app.repositories.postgres import db_projects
from app.services.knowledge.errors import KnowledgeError

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,39}$")   # project_id / building: dùng trong doc_code (nối '_')
PROJECT_FIELDS = ("name", "aliases", "owner_aliases", "source_priority", "governing_language")




def _clean_list(v: Any) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in (v or []) if isinstance(x, str) and x.strip()))


def _check_id(value: str, what: str) -> str:
    value = (value or "").strip()
    if not ID_RE.match(value):
        raise KnowledgeError(f"{what} không hợp lệ: {value!r} (chữ, số, '.', '-'; không có '_' hay khoảng trắng)")
    return value


def _validate(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PROJECT_FIELDS:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "name":
            v = str(v).strip()
            if not v:
                raise KnowledgeError("tên dự án bắt buộc")
        elif k == "governing_language":
            if v not in ("vi", "en"):
                raise KnowledgeError("ngôn ngữ ưu tiên phải là vi hoặc en")
        else:
            v = _clean_list(v)
            if k == "source_priority" and (bad := [x for x in v if x not in Stream.__members__]):
                raise KnowledgeError(f"luồng không hợp lệ trong thứ tự ưu tiên: {bad}")
        out[k] = v
    return out


def regenerate_aliases(conn, project_id: str) -> int:
    """Sinh lại project_aliases của 1 dự án (normalize_alias – như app/db/migrate.py)."""
    return replace_aliases(conn, project_alias_rows(db_projects(conn, [project_id])), project_id)


def get_project(conn, project_id: str) -> dict[str, Any]:
    rows = db_projects(conn, [project_id])
    if not rows:
        raise KnowledgeError(f"không có dự án {project_id}", 404)
    return rows[0]


def list_projects(conn) -> list[dict[str, Any]]:
    return db_projects(conn)


def create_project(conn, data: dict[str, Any]) -> dict[str, Any]:
    pid = _check_id(data.get("project_id", ""), "mã dự án")
    f = _validate({"name": data.get("name") or "", **{k: data.get(k) for k in PROJECT_FIELDS if k != "name"}})
    with conn.transaction():
        if conn.execute("SELECT 1 FROM projects WHERE project_id = %s", (pid,)).fetchone():
            raise KnowledgeError(f"dự án {pid} đã có", 409)
        conn.execute(
            "INSERT INTO projects (project_id, name, aliases, owner_aliases, source_priority, governing_language) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (pid, f["name"], f.get("aliases", []), f.get("owner_aliases", []), f.get("source_priority", []),
             f.get("governing_language", "vi")))
        for b in data.get("buildings") or []:
            _insert_building(conn, pid, b)
        regenerate_aliases(conn, pid)
    return get_project(conn, pid)


def update_project(conn, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    f = _validate(data)
    with conn.transaction():
        get_project(conn, project_id)
        if f:
            sets = ", ".join(f"{k} = %({k})s" for k in f)
            conn.execute(f"UPDATE projects SET {sets}, updated_at = now() WHERE project_id = %(pid)s",
                         {**f, "pid": project_id})
        regenerate_aliases(conn, project_id)
    return get_project(conn, project_id)


def _insert_building(conn, project_id: str, b: dict[str, Any]) -> str:
    building = _check_id(b.get("building", ""), "mã công trình")
    if conn.execute("SELECT 1 FROM project_buildings WHERE project_id = %s AND building = %s",
                    (project_id, building)).fetchone():
        raise KnowledgeError(f"công trình {building} đã có", 409)
    conn.execute("INSERT INTO project_buildings (project_id, building, aliases) VALUES (%s,%s,%s)",
                 (project_id, building, _clean_list(b.get("aliases"))))
    return building


def _touch(conn, project_id: str) -> None:
    conn.execute("UPDATE projects SET updated_at = now() WHERE project_id = %s", (project_id,))


def add_building(conn, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    with conn.transaction():
        get_project(conn, project_id)
        _insert_building(conn, project_id, data)
        _touch(conn, project_id)
        regenerate_aliases(conn, project_id)
    return get_project(conn, project_id)


def update_building(conn, project_id: str, building: str, data: dict[str, Any]) -> dict[str, Any]:
    with conn.transaction():
        cur = conn.execute("UPDATE project_buildings SET aliases = %s WHERE project_id = %s AND building = %s",
                           (_clean_list(data.get("aliases")), project_id, building))
        if cur.rowcount == 0:
            raise KnowledgeError(f"không có công trình {building} trong dự án {project_id}", 404)
        _touch(conn, project_id)
        regenerate_aliases(conn, project_id)
    return get_project(conn, project_id)


def delete_building(conn, project_id: str, building: str) -> dict[str, Any]:
    """Không xóa công trình còn tài liệu hoặc job đang xử lý."""
    with conn.transaction():
        used = conn.execute(
            "SELECT (SELECT count(*) FROM documents WHERE project_id = %(p)s AND building = %(b)s) + "
            "(SELECT count(*) FROM ingest_jobs WHERE project_id = %(p)s AND building = %(b)s "
            " AND status IN ('QUEUED','PROCESSING','DRAFT','PUBLISHING')) AS n",
            {"p": project_id, "b": building}).fetchone()["n"]
        if used:
            raise KnowledgeError(f"công trình {building} còn {used} tài liệu/job – không xóa được", 409)
        cur = conn.execute("DELETE FROM project_buildings WHERE project_id = %s AND building = %s",
                           (project_id, building))
        if cur.rowcount == 0:
            raise KnowledgeError(f"không có công trình {building} trong dự án {project_id}", 404)
        _touch(conn, project_id)
        regenerate_aliases(conn, project_id)
    return get_project(conn, project_id)
