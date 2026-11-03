"""Dự án / công trình (docs/knowledge-admin.md mục 1, 5). Sửa alias → sinh lại project_aliases (dùng ngay trong chat).

    GET/POST /projects · PATCH /projects/{id}
    POST /projects/{id}/buildings · PATCH/DELETE /projects/{id}/buildings/{b}
"""
from __future__ import annotations

from typing import Any, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_deps
from app.db.client import pg_connect
from app.services.knowledge import projects as svc
from app.services.knowledge.cache import clear_profiles
from app.services.knowledge.errors import KnowledgeError
from app.services.query.deps import Deps

router = APIRouter(prefix="/projects", tags=["projects"])


def get_conn(deps: Deps = Depends(get_deps)) -> Iterator:
    """Kết nối autocommit cho thao tác quản trị (mỗi request 1 kết nối)."""
    with pg_connect(deps.settings, autocommit=True) as conn:
        yield conn


def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """KnowledgeError → HTTPException theo status."""
    try:
        return fn(*args, **kwargs)
    except KnowledgeError as e:
        raise HTTPException(e.status, str(e)) from None


class BuildingIn(BaseModel):
    building: str
    aliases: list[str] = []


class BuildingPatch(BaseModel):
    aliases: list[str] = []


class ProjectIn(BaseModel):
    project_id: str
    name: str
    aliases: list[str] = []
    owner_aliases: list[str] = []
    source_priority: list[str] = []
    governing_language: str = "vi"
    buildings: list[BuildingIn] = []


class ProjectPatch(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    owner_aliases: list[str] | None = None
    source_priority: list[str] | None = None
    governing_language: str | None = None


@router.get("")
def list_projects(conn=Depends(get_conn)):
    return svc.list_projects(conn)


@router.post("", status_code=201)
def create_project(body: ProjectIn, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(svc.create_project, conn, body.model_dump())
    clear_profiles(deps)
    return out


@router.patch("/{project_id}")
def update_project(project_id: str, body: ProjectPatch, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(svc.update_project, conn, project_id, body.model_dump(exclude_none=True))
    clear_profiles(deps)
    return out


@router.post("/{project_id}/buildings", status_code=201)
def add_building(project_id: str, body: BuildingIn, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(svc.add_building, conn, project_id, body.model_dump())
    clear_profiles(deps)
    return out


@router.patch("/{project_id}/buildings/{building}")
def update_building(project_id: str, building: str, body: BuildingPatch, conn=Depends(get_conn),
                    deps: Deps = Depends(get_deps)):
    out = call(svc.update_building, conn, project_id, building, body.model_dump())
    clear_profiles(deps)
    return out


@router.delete("/{project_id}/buildings/{building}")
def delete_building(project_id: str, building: str, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(svc.delete_building, conn, project_id, building)
    clear_profiles(deps)
    return out
