"""Record a good run and replay it later (M10 backup): no models, no phones, no internet needed.

A recording is the run's events, from its alert to the end, saved as JSON lines in data/recordings/.
Replaying resets the run and re-emits those events with their original spacing (sped up), updating
household statuses as it goes, so the dashboard shows the run exactly as it happened.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlmodel import select

from app.db import Database
from app.events import EventBus, EventType
from app.models import Event, Household, Status

SKIP = {EventType.RUN_RESET.value, EventType.DEBUG_PING.value}


def recordings_dir(data_dir: Path) -> Path:
    return data_dir / "recordings"


def safe_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    if not clean:
        raise ValueError("name must have letters or digits")
    return clean[:60]


def save_recording(db: Database, data_dir: Path, name: str) -> dict[str, Any]:
    """Save the latest run's events (from its alert.received on) as a recording."""
    with db.session() as session:
        alert_id = session.exec(select(Event.id).where(Event.type == EventType.ALERT_RECEIVED.value)
                                .order_by(Event.id.desc()).limit(1)).first()
        if alert_id is None:
            raise LookupError("no run to record yet")
        events = session.exec(select(Event).where(Event.id >= alert_id).order_by(Event.id)).all()
    events = [e.to_wire() for e in events if e.type not in SKIP]
    path = recordings_dir(data_dir) / f"{safe_name(name)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return {"name": path.stem, "events": len(events), "path": str(path)}


def list_recordings(data_dir: Path) -> list[dict[str, Any]]:
    folder = recordings_dir(data_dir)
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
        out.append({"name": path.stem, "events": count})
    return out


def load_recording(data_dir: Path, name: str) -> list[dict[str, Any]]:
    path = recordings_dir(data_dir) / f"{safe_name(name)}.jsonl"
    if not path.exists():
        raise FileNotFoundError(name)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def first_time(events: list[dict[str, Any]]) -> datetime | None:
    return _when(events[0]) if events else None


def _when(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["time"].replace("Z", "+00:00"))


async def play(db: Database, bus: EventBus, events: list[dict[str, Any]], speed: float) -> None:
    """Re-emit recorded events with their original spacing divided by `speed`."""
    if not events:
        return
    start = _when(events[0])
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for e in events:
        due = (_when(e) - start).total_seconds() / speed
        wait = due - (loop.time() - t0)
        if wait > 0:
            await asyncio.sleep(wait)
        if e["type"] == EventType.HOUSEHOLD_STATUS.value and e.get("household_id") is not None:
            with db.session() as session:
                session.execute(update(Household).where(Household.id == e["household_id"])
                                .values(status=Status(e["payload"]["status"])))
                session.commit()
        payload = {**e["payload"]}
        if e["type"] == EventType.ALERT_RECEIVED.value:
            payload["text"] = f"{payload.get('text', '')} (replay of a recorded run)".strip()
        bus.emit(e["type"], payload, e.get("household_id"))
