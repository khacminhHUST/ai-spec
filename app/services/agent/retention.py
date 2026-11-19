"""Xóa hội thoại không hoạt động quá N ngày (config/agent.yaml `conversations.retention_days`).

Xóa trạng thái LangGraph của thread (checkpointer.delete_thread) trước, rồi mới xóa dòng `conversations`
– lỗi giữa chừng thì lần chạy sau vẫn thấy hội thoại đó và xóa tiếp, không để sót checkpoint mồ côi.
"""
from __future__ import annotations

import logging
from typing import Any

from app.repositories.postgres import Repo

log = logging.getLogger(__name__)


def purge_stale_conversations(repo: Repo, checkpointer: Any, days: int) -> int:
    """→ số hội thoại đã xóa. days <= 0: tắt (giữ vĩnh viễn)."""
    if days <= 0:
        return 0
    n = 0
    for cid in repo.stale_conversations(days):
        try:
            checkpointer.delete_thread(cid)
            repo.delete_conversation(cid)
            n += 1
        except Exception as e:  # noqa: BLE001 – lần chạy sau thử lại
            log.warning("Xóa hội thoại cũ %s lỗi: %s", cid, e)
    if n:
        log.info("Đã xóa %d hội thoại không hoạt động quá %d ngày", n, days)
    return n
