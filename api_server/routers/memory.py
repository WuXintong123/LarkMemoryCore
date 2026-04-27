"""Memory engine admin routes."""

from __future__ import annotations

from importlib import import_module
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..dependencies.auth import ApiKeyPrincipal, require_api_scopes
from ..schemas.memory import MemoryEventInput


router = APIRouter()


def _main_module():
    return import_module("api_server.main")


@router.post("/v1/memory/events")
async def ingest_memory_event(
    request: MemoryEventInput,
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    return main_module.memory_engine.ingest_event(request)


@router.get("/v1/memory/search")
async def search_memory(
    query: str = Query(..., min_length=1),
    tenant_id: str = Query(default="default"),
    project_id: str = Query(default="default"),
    conversation_id: str = Query(default=""),
    limit: int = Query(default=3, ge=1, le=20),
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    return main_module.memory_engine.search(
        tenant_id=tenant_id,
        project_id=project_id,
        conversation_id=conversation_id,
        query=query,
        limit=limit,
    )


@router.get("/v1/memory/report")
async def memory_report(
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    return main_module.memory_engine.report()
