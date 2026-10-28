"""Truy cập dữ liệu (Postgres) cho Query Pipeline.

`Repo` là interface; `PgRepo` là bản thật (bảng `documents`, `parent_chunks`, `project_aliases`,
`conversations` – shared/migrations). Unit test dùng bản in-memory (tests/query/fakes.py).
Quyền truy cập dự án: chỉ theo `test_users` trong config/projects.yaml (chưa làm ACL chi tiết).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from shared.config import Settings
from shared.keys import normalize_alias

PARENT_COLS = ("parent_id, doc_id, section_key, parent_section_key, lang, type, number, title, breadcrumb, "
               "content_md, page_start, page_end, referenced_ids, token_count")
DOC_COLS = "doc_id, doc_code, project_id, building, stream, disciplines, title, issuer, revision, languages, is_latest"


def section_matches(section_key: str, ref: str) -> bool:
    """'table_4_3' khớp 'table_4_3' và 'part_i__table_4_3' (section_key có scope)."""
    return section_key == ref or section_key.endswith("__" + ref)


class Repo(ABC):
    # ---- dự án / quyền ----
    @abstractmethod
    def user_projects(self, user_id: str) -> list[str]: ...

    @abstractmethod
    def aliases(self, project_ids: list[str]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def project_info(self, project_ids: list[str]) -> dict[str, dict[str, Any]]:
        """project_id → {name, aliases (chính), source_priority, governing_language}."""

    @abstractmethod
    def documents(self, project_ids: list[str]) -> list[dict[str, Any]]:
        """Tài liệu is_latest của các dự án."""

    # ---- parent chunks ----
    @abstractmethod
    def parents_by_ids(self, parent_ids: list[str]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def parents_by_sections(self, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Mọi ngôn ngữ của các (doc_id, section_key) – 1 truy vấn."""

    @abstractmethod
    def find_sections(self, doc_ids: list[str], refs: list[str]) -> list[dict[str, Any]]:
        """Parent có section_key khớp ref (kể cả có scope) trong các tài liệu cho trước."""

    @abstractmethod
    def standards_in_system(self, codes: list[str]) -> dict[str, list[str]]:
        """Mã tiêu chuẩn → doc_id tài liệu có trong hệ thống (theo title/doc_code)."""

    # ---- hội thoại ----
    @abstractmethod
    def upsert_conversation(self, conversation_id: str, user_id: str, title: str | None) -> None: ...

    @abstractmethod
    def list_conversations(self, user_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None: ...


def config_user_projects(settings: Settings, user_id: str) -> list[str]:
    for u in settings.projects.get("test_users", []):
        if u.get("user_id") == user_id:
            return list(u.get("projects", []))
    return []


def config_project_info(settings: Settings, project_ids: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for p in settings.project_list():
        if p["project_id"] in project_ids:
            buildings = [b["building"] for b in p.get("buildings", [])]
            out[p["project_id"]] = {
                "name": p.get("name") or p["project_id"],
                "aliases": [*buildings, *p.get("aliases", [])[:3]],
                "source_priority": p.get("source_priority", []),
                "governing_language": p.get("governing_language", "vi"),
            }
    return out


def _std_norm(code: str) -> str:
    return normalize_alias(code.split(":")[0].split("-")[0])


class PgRepo(Repo):
    def __init__(self, settings: Settings, pool):
        self.settings = settings
        self.pool = pool

    def _q(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return list(conn.execute(sql, params))

    def user_projects(self, user_id: str) -> list[str]:
        return config_user_projects(self.settings, user_id)

    def aliases(self, project_ids: list[str]) -> list[dict[str, Any]]:
        if not project_ids:
            return []
        return self._q("SELECT alias_norm, alias, project_id, building, kind FROM project_aliases "
                       "WHERE project_id = ANY(%s)", (project_ids,))

    def project_info(self, project_ids: list[str]) -> dict[str, dict[str, Any]]:
        info = config_project_info(self.settings, project_ids)
        for pid in project_ids:  # dự án chỉ có trong project_aliases (VD dự án giả của test)
            if pid not in info:
                names = [r["alias"] for r in self.aliases([pid])]
                info[pid] = {"name": names[0] if names else pid, "aliases": names[:3],
                             "source_priority": [], "governing_language": "vi"}
        return info

    def documents(self, project_ids: list[str]) -> list[dict[str, Any]]:
        if not project_ids:
            return []
        return self._q(f"SELECT {DOC_COLS} FROM documents WHERE project_id = ANY(%s) AND is_latest", (project_ids,))

    def parents_by_ids(self, parent_ids: list[str]) -> list[dict[str, Any]]:
        if not parent_ids:
            return []
        return self._q(f"SELECT {PARENT_COLS} FROM parent_chunks WHERE parent_id = ANY(%s)", (list(parent_ids),))

    def parents_by_sections(self, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        if not pairs:
            return []
        keys = [f"{d}\x1f{s}" for d, s in pairs]
        return self._q(f"SELECT {PARENT_COLS} FROM parent_chunks WHERE doc_id || chr(31) || section_key = ANY(%s)",
                       (keys,))

    def find_sections(self, doc_ids: list[str], refs: list[str]) -> list[dict[str, Any]]:
        if not doc_ids or not refs:
            return []
        likes = [f"%\\_\\_{r.replace('_', chr(92) + '_')}" for r in refs]
        return self._q(
            f"SELECT {PARENT_COLS} FROM parent_chunks WHERE doc_id = ANY(%s) "
            "AND (section_key = ANY(%s) OR section_key LIKE ANY(%s))",
            (list(doc_ids), list(refs), likes),
        )

    def standards_in_system(self, codes: list[str]) -> dict[str, list[str]]:
        if not codes:
            return {}
        rows = self._q("SELECT doc_id, coalesce(title,'') || ' ' || coalesce(doc_code,'') AS t FROM documents "
                       "WHERE is_latest")
        out: dict[str, list[str]] = {}
        for code in codes:
            key = _std_norm(code)
            hit = [r["doc_id"] for r in rows if key and key in normalize_alias(r["t"])]
            if hit:
                out[code] = hit
        return out

    def upsert_conversation(self, conversation_id: str, user_id: str, title: str | None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO conversations (conversation_id, user_id, title) VALUES (%s, %s, %s) "
                "ON CONFLICT (conversation_id) DO UPDATE SET updated_at = now()",
                (conversation_id, user_id, (title or "")[:200] or None),
            )

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        return self._q("SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations "
                       "WHERE user_id = %s ORDER BY updated_at DESC", (user_id,))

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations "
                       "WHERE conversation_id = %s", (conversation_id,))
        return rows[0] if rows else None
