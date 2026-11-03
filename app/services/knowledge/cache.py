"""Làm mới cache hồ sơ dự án của Query Pipeline (Deps.cached_profile, TTL profile_cache_ttl_s) – làm NGOÀI
app/services/query/.

- Thao tác trong cùng tiến trình API (đặt hiệu lực, gỡ, xóa, sửa dự án) → clear_profiles(deps).
- Worker là tiến trình riêng → "phiên bản kho" (store_version) được kiểm tra trước mỗi lần dùng cache:
  attach_store_version(deps) bọc deps.cached_profile; phiên bản đổi → xóa cache rồi nạp lại.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

STORE_VERSION_SQL = (
    "SELECT (SELECT count(*) FROM documents) AS n_docs, (SELECT max(ingested_at) FROM documents) AS last_ingest, "
    "(SELECT md5(coalesce(string_agg(doc_id, ',' ORDER BY doc_id), '')) FROM documents WHERE is_latest) AS latest, "
    "(SELECT max(updated_at) FROM projects) AS projects_at"
)


def store_version(conn) -> tuple:
    """Đổi khi thêm/xóa tài liệu, đổi is_latest hoặc sửa dự án."""
    return tuple(conn.execute(STORE_VERSION_SQL).fetchone().values())


def clear_profiles(deps: Any) -> None:
    cache = getattr(deps, "_profile_cache", None)
    if cache is not None:
        cache.clear()


def attach_store_version(deps: Any, version_fn: Callable[[], Any] | None = None) -> None:
    """Bọc deps.cached_profile: kiểm tra phiên bản kho trước khi dùng cache (mặc định đọc qua deps.repo.pool)."""
    if getattr(deps, "_store_version_attached", False):
        return
    if version_fn is None:
        pool = deps.repo.pool

        def version_fn() -> tuple:
            with pool.connection() as conn:
                return store_version(conn)

    original = deps.cached_profile
    state: dict[str, Any] = {"v": None}

    def cached_profile(project_id: str, loader: Callable[[], dict]) -> dict:
        try:
            v = version_fn()
        except Exception as e:  # noqa: BLE001 – lỗi đọc phiên bản: dùng cache như cũ (TTL)
            log.warning("Không đọc được phiên bản kho: %s", e)
            return original(project_id, loader)
        if v != state["v"]:
            clear_profiles(deps)
            state["v"] = v
        return original(project_id, loader)

    deps.cached_profile = cached_profile
    deps._store_version_attached = True
