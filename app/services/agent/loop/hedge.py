"""Chống đuôi chậm token đầu của LLM agent (hedged request).

Đo trên CT05: token đầu của 1 bước LLM agent p50 ~2.3s, p95 ~4.6s nhưng ~2% bước chờ 9–31s (cả bước gọi tool lẫn bước
trả lời) – do phía server, gọi lại thường ra trong ~3s. Cách xử lý: bắt đầu stream; quá `first_token_timeout_s` mà chưa
có chunk nào → gửi thêm 1 request y hệt (request cũ vẫn chạy); lấy request nào ra chunk đầu trước, bỏ request kia.

Không trùng token trên SSE: việc chọn diễn ra TRƯỚC khi chunk đầu tiên được trả ra khỏi `_stream`; chỉ chunk của request
thắng được yield (langchain-core phát callback `on_llm_new_token` → sự kiện `token` theo chunk được yield). Request
chạy trong thread riêng, không nhận run_manager → không tự phát callback.
Lỗi: request đầu lỗi trước hạn → ném lỗi ngay (như cũ, SDK đã tự thử lại); đã gửi request thứ 2 → chờ request còn lại,
cả 2 lỗi → ném lỗi của request đầu. Request thắng lỗi giữa chừng → ném lỗi (như cũ).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Iterator, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI

log = logging.getLogger(__name__)
T = TypeVar("T")
_ITEM, _END, _ERR = "item", "end", "err"


def note_hedge(attempt: int, waited_s: float) -> None:
    """Điểm móc để đo (tests/eval patch hàm này)."""
    log.warning("LLM chưa ra token đầu sau %.1fs – gửi thêm request thứ %d", waited_s, attempt + 1)


def note_hedge_winner(winner: int, waited_s: float) -> None:
    """Điểm móc để đo: request nào ra chunk đầu trước (0 = request gốc) sau khi đã gửi thêm request."""
    log.info("Chunk đầu sau %.1fs từ request thứ %d", waited_s, winner + 1)


def hedged_stream(start: Callable[[], Iterator[T]], first_token_timeout_s: float, max_hedges: int = 1,
                  clock: Callable[[], float] = time.monotonic) -> Iterator[T]:
    """start() → iterator mới (1 request). Chunk đầu chậm quá hạn → chạy thêm request, dùng request ra chunk đầu trước."""
    q: queue.Queue = queue.Queue()
    stops: list[threading.Event] = []

    def launch() -> None:
        i, stop = len(stops), threading.Event()
        stops.append(stop)

        def run() -> None:
            it = None
            try:
                it = start()
                for item in it:
                    if stop.is_set():
                        return
                    q.put((i, _ITEM, item))
                q.put((i, _END, None))
            except BaseException as e:  # noqa: BLE001 – chuyển lỗi về thread gọi
                q.put((i, _ERR, e))
            finally:
                close = getattr(it, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        threading.Thread(target=run, daemon=True, name=f"llm-hedge-{i}").start()

    t0 = clock()
    launch()
    errors: dict[int, BaseException] = {}
    winner: int | None = None
    try:
        while winner is None:
            can_hedge = len(stops) <= max_hedges
            try:
                if can_hedge:
                    i, kind, val = q.get(timeout=max(0.0, t0 + first_token_timeout_s * len(stops) - clock()))
                else:
                    i, kind, val = q.get()
            except queue.Empty:
                note_hedge(len(stops), clock() - t0)
                launch()
                continue
            if kind == _ERR:
                errors[i] = val
                if len(errors) == len(stops):
                    raise errors[0]
                continue
            winner = i
            if len(stops) > 1:
                note_hedge_winner(i, clock() - t0)
            for j, s in enumerate(stops):
                if j != i:
                    s.set()
            if kind == _END:
                return
            yield val
        while True:
            i, kind, val = q.get()
            if i != winner:
                continue
            if kind == _ITEM:
                yield val
            elif kind == _END:
                return
            else:
                raise val
    finally:
        for s in stops:
            s.set()


class HedgedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI + hedged request cho chunk đầu (chỉ đường stream – agent luôn stream). 0 = tắt."""

    first_token_timeout_s: float = 0.0

    def _stream(self, messages: list, stop: list[str] | None = None, run_manager: Any = None,
                **kwargs: Any) -> Iterator[Any]:
        if not self.first_token_timeout_s or self.first_token_timeout_s <= 0:
            yield from super()._stream(messages, stop, run_manager, **kwargs)
            return
        parent = super()
        for chunk in hedged_stream(lambda: parent._stream(messages, stop, None, **kwargs), self.first_token_timeout_s):
            if run_manager:                      # giữ hành vi lớp cha khi được gọi kèm run_manager
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk
