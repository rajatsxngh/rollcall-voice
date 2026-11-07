"""GET /state: the snapshot the dashboard loads before streaming events (see docs/API.md)."""
from typing import Any

from fastapi import APIRouter, Request

from app.snapshot import build_state

router = APIRouter(tags=["state"])


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    state = request.app.state
    return build_state(state.db, state.bus, run=state.live_run.info())
