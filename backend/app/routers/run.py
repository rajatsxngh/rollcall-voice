"""Run controls: POST /alerts/demo and POST /reset (see docs/API.md)."""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app import recording
from app.clock import RealClock
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
    _stop_replay(request.app.state)
    if live.active:
        raise HTTPException(status_code=409, detail="A run is already in progress. POST /reset first.")
    if live.engine is not None or await run_in_threadpool(run_needs_reset, live.db):
        live.reset()
    info = await live.start(speed, lines, policy)
    return {"started": True, **info}


def _stop_replay(state) -> None:
    task = getattr(state, "replay_task", None)
    if task is not None and not task.done():
        task.cancel()
    state.replay_task = None


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    _stop_replay(request.app.state)
    request.app.state.live_run.reset()
    return {"reset": True}


# --- recordings (M10 backup: replay a good run with no models, phones or internet) -------------


@router.get("/recordings")
def recordings(request: Request) -> dict[str, Any]:
    return {"recordings": recording.list_recordings(request.app.state.settings.data_dir)}


@router.post("/recordings/save")
def save(request: Request, name: str = Query(..., min_length=1, max_length=60)) -> dict[str, Any]:
    """Save the latest run (from its alert on) as a recording."""
    try:
        return recording.save_recording(request.app.state.db, request.app.state.settings.data_dir, name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/recordings/{name}/play")
async def play(request: Request, name: str, speed: float = Query(10.0, gt=0, le=1000)) -> dict[str, Any]:
    """Reset, then replay a recording with its original spacing divided by `speed`."""
    state = request.app.state
    try:
        events = recording.load_recording(state.settings.data_dir, name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such recording") from None
    _stop_replay(state)
    state.live_run.reset()
    state.bus.now = RealClock(speed, start=recording.first_time(events)).now  # replayed events keep run time
    state.replay_task = asyncio.create_task(recording.play(state.db, state.bus, events, speed))
    return {"playing": name, "events": len(events), "speed": speed}
