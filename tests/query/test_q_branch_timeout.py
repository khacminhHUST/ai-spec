"""Unit – timeout nhánh retrieval tính từ lúc nhánh chạy, không tính thời gian chờ trong hàng đợi pool."""
import threading
import time
from types import SimpleNamespace

import pytest

import app.services.query.retrieval as R

pytestmark = pytest.mark.unit


def _deps(search):
    return SimpleNamespace(cfg=lambda k: {"branch_timeout_s": 0.3}, retriever=SimpleNamespace(search=search))


BRANCH = {"project_id": "P", "stream": "DESIGN_CONSULTANT", "lang": "vi"}


def test_queue_wait_not_counted(monkeypatch):
    monkeypatch.setattr(R, "_POOL", R.ThreadPoolExecutor(max_workers=1))
    gate = threading.Event()
    R._POOL.submit(lambda: gate.wait(0.5))               # chiếm worker duy nhất 0.5s > branch_timeout 0.3s
    hit = {"child_id": "c", "parent_id": "p", "doc_id": "d", "section_key": "s", "lang": "vi", "stream": "X",
           "project_id": "P", "text": "t", "fusion_score": 1.0}
    out = R.run_branch(_deps(lambda **kw: [hit]), BRANCH, {"queries_vi": ["q"]})
    assert len(out) == 1                                  # trước đây: chờ hàng đợi bị tính → [] (NOT_FOUND giả)


def test_slow_branch_still_times_out():
    out = R.run_branch(_deps(lambda **kw: time.sleep(1) or []), BRANCH, {"queries_vi": ["q"]})
    assert out == []
