"""Run controls: POST /alerts/demo and POST /reset (see docs/API.md).

Until M3, the run is driven by app.stub_engine. M3 swaps in the real engine behind the same routes.
"""
import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app import stub_engine
from app.events import EventType
from app.geo import WarningArea

router = APIRouter(tags=["run"])
log = logging.getLogger(__name__)


async def stop_run(state) -> None:
    task = getattr(state, "run_task", None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _reset(state) -> None:
    await stop_run(state)
    await run_in_threadpool(stub_engine.reset_run, state.db)
    state.bus.emit(EventType.RUN_RESET, {})


@router.post("/alerts/demo")
async def start_demo(request: Request, speed: float = Query(10.0, gt=0, le=1000)) -> dict[str, Any]:
    """Start the response on the saved warning area. A finished earlier run is reset first."""
    state = request.app.state
    task = getattr(state, "run_task", None)
    if task and not task.done():
        raise HTTPException(status_code=409, detail="A run is already in progress. POST /reset first.")
    if await run_in_threadpool(stub_engine.needs_reset, state.db):
        await _reset(state)
    area = WarningArea.load(state.settings.data_dir / "warning_area.geojson")

    async def runner() -> None:
        try:
            await stub_engine.run(state.db, state.bus, area, speed)
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the server up; the log says what broke
            log.exception("stub run failed")

    state.run_task = asyncio.create_task(runner())
    return {"started": True, "engine": "stub", "speed": speed}


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    await _reset(request.app.state)
    return {"reset": True}
