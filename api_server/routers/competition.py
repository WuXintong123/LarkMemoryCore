"""Competition delivery evidence routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from competition.feishu_office.evidence import load_feishu_office_evidence

from ..dependencies.auth import ApiKeyPrincipal, require_api_scopes


router = APIRouter()


@router.get("/v1/competition/feishu-office/evidence")
async def feishu_office_evidence(
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    return load_feishu_office_evidence()
