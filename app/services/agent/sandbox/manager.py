"""Vòng đời sandbox: 1 sandbox / conversation, tạo khi cần, hủy khi idle > idle_ttl_s hoặc khi xóa conversation.
Trần max_sandboxes (0 = không giới hạn): đủ trần → SandboxBusy, không tạo thêm (tránh hết RAM server)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from app.services.agent.sandbox.base import SandboxBackend, SandboxBusy

log = logging.getLogger(__name__)

Factory = Callable[[str], SandboxBackend]


class SandboxManager:
    def __init__(self, factory: Factory, idle_ttl_s: float, clock: Callable[[], float] = time.monotonic,
                 max_sandboxes: int = 0):
        self.factory = factory
        self.idle_ttl_s = idle_ttl_s
        self.max_sandboxes = max_sandboxes
        self.clock = clock
        self._items: dict[str, tuple[SandboxBackend, float]] = {}
        self._lock = threading.Lock()

    def get(self, conversation_id: str) -> SandboxBackend:
        """Sandbox của conversation (tạo nếu chưa có), cập nhật thời điểm dùng."""
        self.reap_idle()
        with self._lock:
            item = self._items.get(conversation_id)
            if not item and self.max_sandboxes and len(self._items) >= self.max_sandboxes:
                log.warning("Đủ trần %d sandbox – từ chối hội thoại %s", self.max_sandboxes, conversation_id)
                raise SandboxBusy("Hệ thống đang bận (đủ số phiên xử lý file cùng lúc). Vui lòng thử lại sau ít phút.")
            sbx = item[0] if item else self.factory(conversation_id)
            if not item:
                sbx.create()
            self._items[conversation_id] = (sbx, self.clock())
            return sbx

    def peek(self, conversation_id: str) -> SandboxBackend | None:
        """Sandbox đang sống (không tạo mới) – dùng cho tải file."""
        with self._lock:
            item = self._items.get(conversation_id)
            if item:
                self._items[conversation_id] = (item[0], self.clock())
            return item[0] if item else None

    def destroy(self, conversation_id: str) -> bool:
        with self._lock:
            item = self._items.pop(conversation_id, None)
        if item:
            self._safe_destroy(conversation_id, item[0])
        return item is not None

    def reap_idle(self) -> list[str]:
        now = self.clock()
        with self._lock:
            idle = [cid for cid, (_, t) in self._items.items() if now - t > self.idle_ttl_s]
            items = [(cid, self._items.pop(cid)[0]) for cid in idle]
        for cid, sbx in items:
            self._safe_destroy(cid, sbx)
        return idle

    def close_all(self) -> None:
        with self._lock:
            items = list(self._items.items())
            self._items.clear()
        for cid, (sbx, _) in items:
            self._safe_destroy(cid, sbx)

    @staticmethod
    def _safe_destroy(cid: str, sbx: SandboxBackend) -> None:
        try:
            sbx.destroy()
        except Exception:  # noqa: BLE001
            log.exception("hủy sandbox %s lỗi", cid)


def build_manager(cfg: dict[str, Any]) -> SandboxManager:
    """cfg = agent.yaml › sandbox. backend: docker | mock."""
    if cfg.get("backend") == "mock":
        from app.services.agent.sandbox.mock_backend import MockSandbox

        factory: Factory = lambda cid: MockSandbox()  # noqa: E731
    else:
        from app.services.agent.sandbox.docker_backend import DockerSandbox

        factory = lambda cid: DockerSandbox(cid, cfg)  # noqa: E731
    return SandboxManager(factory, float(cfg.get("idle_ttl_s", 1800)), max_sandboxes=int(cfg.get("max_sandboxes", 0)))
