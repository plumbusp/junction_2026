"""GET /api/metrics: serve ``backend/eval/results/latest.json`` as-is.

``latest.json`` is written only by live eval runs. Stub results live under
``backend/eval/results/stub/`` and are never read here; a ``latest.json`` that says
``"mode": "stub"`` is refused as well. No real results: 404 JSON.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.config import REPO_ROOT

log = logging.getLogger(__name__)
router = APIRouter()

LATEST_RESULTS = REPO_ROOT / "backend" / "eval" / "results" / "latest.json"
NO_RESULTS = "No real evaluation results exist yet."


@router.get("/api/metrics")
def get_metrics(request: Request) -> JSONResponse:
    path = request.app.state.metrics_path
    if not path.is_file():
        raise ApiError(404, "no_results", NO_RESULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("cannot read metrics results: %s", type(exc).__name__)
        raise ApiError(
            503, "metrics_unavailable", "The evaluation results could not be read."
        ) from None
    if not isinstance(data, dict) or data.get("mode") == "stub":
        raise ApiError(404, "no_results", NO_RESULTS)
    return JSONResponse(data, headers={"Cache-Control": "no-cache"})
