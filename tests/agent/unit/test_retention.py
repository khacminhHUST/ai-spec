"""Xóa hội thoại không hoạt động quá N ngày: xóa cả checkpoint LangGraph lẫn dòng conversations."""
from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.services.agent.retention import purge_stale_conversations
from tests.query.fakes import InMemoryRepo

pytestmark = pytest.mark.unit


def _checkpoint(cp: InMemorySaver, thread_id: str) -> None:
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    cp.put(cfg, {"v": 1, "id": f"ck-{thread_id}", "ts": "", "channel_values": {}, "channel_versions": {},
                 "versions_seen": {}, "pending_sends": []}, {}, {})


def _has_thread(cp: InMemorySaver, thread_id: str) -> bool:
    return cp.get_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}) is not None


def test_purge_removes_only_stale_conversations():
    repo, cp, now = InMemoryRepo(None), InMemorySaver(), datetime.now(timezone.utc)
    for cid, age in (("old", 31), ("new", 2)):
        repo.upsert_conversation(cid, "u1", cid)
        repo.conversations[cid]["updated_at"] = now - timedelta(days=age)
        _checkpoint(cp, cid)

    assert purge_stale_conversations(repo, cp, 30) == 1
    assert repo.get_conversation("old") is None and not _has_thread(cp, "old")
    assert repo.get_conversation("new") is not None and _has_thread(cp, "new")


def test_purge_disabled_and_failure_keeps_row():
    repo, now = InMemoryRepo(None), datetime.now(timezone.utc)
    repo.upsert_conversation("old", "u1", "old")
    repo.conversations["old"]["updated_at"] = now - timedelta(days=90)
    assert purge_stale_conversations(repo, InMemorySaver(), 0) == 0           # 0 = giữ vĩnh viễn

    class Broken:
        def delete_thread(self, _):
            raise RuntimeError("db down")

    assert purge_stale_conversations(repo, Broken(), 30) == 0
    assert repo.get_conversation("old") is not None                           # lần sau thử lại
