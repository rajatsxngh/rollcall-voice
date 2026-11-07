"""The event log: every change is a row in `events` plus a push to every WebSocket subscriber.

The dashboard draws only from events, so any run can be replayed from its database file.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, insert
from sqlalchemy import select as sa_select
from sqlmodel import select

from app.db import Database, iso_utc, utcnow
from app.models import Event

EVENTS = Event.__table__


class EventType(StrEnum):
    ALERT_RECEIVED = "alert.received"
    PLAN_CREATED = "plan.created"
    CALL_STARTED = "call.started"
    CALL_TRANSCRIPT = "call.transcript"
    CALL_ENDED = "call.ended"
    NEED_RECORDED = "need.recorded"
    HOUSEHOLD_STATUS = "household.status"
    ACTION_CREATED = "action.created"
    ACTION_UPDATED = "action.updated"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    SMS_SENT = "sms.sent"
    SMS_RECEIVED = "sms.received"
    FOLLOWUP_SCHEDULED = "followup.scheduled"
    AGENT_NOTE = "agent.note"
    STATUS_UPDATE = "status.update"
    RUN_RESET = "run.reset"
    DEBUG_PING = "debug.ping"


# What each event means and the payload it carries. Later milestones may add fields.
EVENT_DOCS: dict[EventType, str] = {
    EventType.ALERT_RECEIVED: "A warning started the response. payload: source, title, polygon",
    EventType.PLAN_CREATED: "The call plan was made or changed. payload: summary, waves, affected",
    EventType.CALL_STARTED: "A call or simulated call began. payload: call_id, channel, attempt",
    EventType.CALL_TRANSCRIPT: "One line of a live call. payload: call_id, role, text",
    EventType.CALL_ENDED: "A call finished. payload: call_id, outcome, summary",
    EventType.NEED_RECORDED: "The agent saved a need. payload: category, details, urgency",
    EventType.HOUSEHOLD_STATUS: "A household's status changed. payload: status, previous",
    EventType.ACTION_CREATED: "Help was booked or scheduled. payload: action_id, type, status, resource_id, reason",
    EventType.ACTION_UPDATED: "An action changed state. payload: action_id, status",
    EventType.APPROVAL_REQUESTED: "A costly action waits for the coordinator. payload: action_id, cost, reason",
    EventType.APPROVAL_DECIDED: "The coordinator approved or rejected. payload: action_id, approved, by",
    EventType.SMS_SENT: "A text went out. payload: to, body",
    EventType.SMS_RECEIVED: "A text came in. payload: from, body",
    EventType.FOLLOWUP_SCHEDULED: "The next call or text was set. payload: at, channel, step",
    EventType.AGENT_NOTE: "A plain-words line for the agent feed. payload: text, reason",
    EventType.STATUS_UPDATE: "Periodic summary for the coordinator. payload: text, counts",
    EventType.RUN_RESET: "The run was reset; clients reload their state. payload: {}",
    EventType.DEBUG_PING: "Test event from scripts/events_cli.py. payload: anything",
}


class Subscription:
    """One WebSocket listener: a bounded queue, fed on the listener's own event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize)
        self.overflowed = False

    def push(self, event: dict[str, Any]) -> None:
        """Runs on self.loop. On overflow, drop the backlog and queue None so the reader reconnects."""
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)


class EventBus:
    def __init__(
        self,
        db: Database,
        now: Callable[[], datetime] = utcnow,
        queue_size: int = 10_000,
    ) -> None:
        self.db = db
        self.now = now  # the run's clock; a simulated clock in test runs
        self._queue_size = queue_size
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()  # keeps id order and delivery order identical

    def emit(
        self,
        type: EventType | str,
        payload: dict[str, Any] | None = None,
        household_id: int | None = None,
    ) -> dict[str, Any]:
        """Store an event and push it to every subscriber. Safe to call from any thread."""
        event_type = EventType(type)  # rejects unknown types
        body = jsonable_encoder(payload or {})
        with self._lock:
            ts = self.now()
            with self.db.engine.begin() as conn:
                result = conn.execute(
                    insert(EVENTS).values(
                        time=ts, type=event_type.value, household_id=household_id, payload=body
                    )
                )
                event_id = result.inserted_primary_key[0]
            event = {
                "id": event_id,
                "time": iso_utc(ts),
                "type": event_type.value,
                "household_id": household_id,
                "payload": body,
            }
            for sub in list(self._subs):
                try:
                    sub.loop.call_soon_threadsafe(sub.push, event)
                except RuntimeError:  # that subscriber's loop has closed
                    self._subs.discard(sub)
        return event

    @contextmanager
    def subscribe(self) -> Iterator[Subscription]:
        sub = Subscription(asyncio.get_running_loop(), self._queue_size)
        with self._lock:
            self._subs.add(sub)
        try:
            yield sub
        finally:
            with self._lock:
                self._subs.discard(sub)

    def since(
        self, last_id: int = 0, limit: int = 1000, household_id: int | None = None
    ) -> list[dict[str, Any]]:
        query = select(Event).where(Event.id > last_id)
        if household_id is not None:
            query = query.where(Event.household_id == household_id)
        with self.db.session() as session:
            rows = session.exec(query.order_by(Event.id).limit(limit)).all()
        return [row.to_wire() for row in rows]

    def last_id(self) -> int:
        with self.db.engine.connect() as conn:
            return conn.execute(sa_select(func.max(EVENTS.c.id))).scalar() or 0
