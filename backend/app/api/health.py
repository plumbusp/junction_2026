"""GET /health and GET /api/health: liveness plus the core mode, nothing else."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
def health(request: Request) -> dict:
    return {"ok": True, "core_mode": request.app.state.settings.core_mode}
