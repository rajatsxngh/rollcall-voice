"""Event history, the live WebSocket, and a debug endpoint scripts use to inject events."""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.events import EventBus, EventType

router = APIRouter(tags=["events"])
CATCH_UP_PAGE = 1000


@router.get("/events")
def list_events(
    request: Request,
    since: int = 0,
    limit: int = Query(1000, ge=1, le=5000),
    household_id: int | None = None,
) -> dict[str, Any]:
    bus: EventBus = request.app.state.bus
    return {"events": bus.since(since, limit, household_id), "last_id": bus.last_id()}


class EmitBody(BaseModel):
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    household_id: int | None = None


@router.post("/debug/emit")
def debug_emit(body: EmitBody, request: Request) -> dict[str, Any]:
    """Inject an event (scripts, the dashboard's fake stream). Off when DEBUG_ENDPOINTS=false."""
    if not request.app.state.settings.debug_endpoints:
        raise HTTPException(status_code=404)
    return request.app.state.bus.emit(body.type, body.payload, body.household_id)


@router.websocket("/ws")
async def events_ws(ws: WebSocket, since: int | None = None) -> None:
    """Live events. With ?since=<id>, first replays everything after that id, then streams.

    The dashboard loads its snapshot, connects with since=<snapshot's last event id>, and on any
    drop reconnects with the last id it saw, so nothing is missed or repeated.
    """
    bus: EventBus = ws.app.state.bus
    # Subscribe before accepting: anything emitted once the client is connected is queued.
    with bus.subscribe() as sub:
        last = since if since is not None else await run_in_threadpool(bus.last_id)
        await ws.accept()
        try:
            if since is not None:
                while True:
                    page = await run_in_threadpool(bus.since, last, CATCH_UP_PAGE)
                    for event in page:
                        await ws.send_json(event)
                        last = event["id"]
                    if len(page) < CATCH_UP_PAGE:
                        break
            disconnected = asyncio.create_task(_wait_for_disconnect(ws))
            try:
                while True:
                    getter = asyncio.create_task(sub.queue.get())
                    done, _ = await asyncio.wait(
                        {getter, disconnected}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if disconnected in done:
                        getter.cancel()
                        return
                    event = getter.result()
                    if event is None:  # this client fell too far behind
                        await ws.close(code=1013, reason="lagging; reconnect with ?since=<last id>")
                        return
                    if event["id"] > last:  # skip anything already sent during catch-up
                        await ws.send_json(event)
                        last = event["id"]
            finally:
                disconnected.cancel()
        except WebSocketDisconnect:
            return


async def _wait_for_disconnect(ws: WebSocket) -> None:
    """Read (and ignore) client messages until the client goes away."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
