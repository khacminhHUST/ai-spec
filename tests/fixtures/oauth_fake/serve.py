"""Chạy ASGI app bằng uvicorn trong thread nền (cổng ngẫu nhiên 127.0.0.1) – cho integration test."""
from __future__ import annotations

import socket
import threading
import time

import uvicorn


def reserve() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


class Served:
    def __init__(self, app, sock: socket.socket):
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("uvicorn không khởi động được")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
