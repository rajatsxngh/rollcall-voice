"""Run controls: POST /alerts/demo and POST /reset (see docs/API.md)."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.engine.runs import LiveRun, run_needs_reset
from app.models import CallPolicy

router = APIRouter(tags=["run"])


@router.post("/alerts/demo")
async def start_demo(
    request: Request,
    speed: float = Query(10.0, gt=0, le=1000, description="seconds on the run's clock per real second"),
    lines: int = Query(100, ge=1, le=1000, description="calls at once"),
    policy: CallPolicy = CallPolicy.RISK_RANKED,
) -> dict[str, Any]:
    """Start the response on the saved warning area. A finished earlier run is reset first."""
    live: LiveRun = request.app.state.live_run
    if live.active:
        raise HTTPException(status_code=409, detail="A run is already in progress. POST /reset first.")
    if live.engine is not None or await run_in_threadpool(run_needs_reset, live.db):
        live.reset()
    info = await live.start(speed, lines, policy)
    return {"started": True, **info}


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    request.app.state.live_run.reset()
    return {"reset": True}
