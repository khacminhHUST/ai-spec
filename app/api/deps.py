"""Dependency FastAPI: lấy graph/deps đã khởi tạo trong lifespan (app.state)."""
from __future__ import annotations

from fastapi import Request

from app.services.query.deps import Deps


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def get_graph(request: Request):
    return request.app.state.graph
