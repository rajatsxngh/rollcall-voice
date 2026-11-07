"""TEMPORARY stand-in for the response engine, until M3 replaces it.

It walks the affected households in risk order and sets each outcome straight from the hidden
truth, with no AI, so the dashboard (M4) can be built against real data and real events. M3 keeps
the same endpoints (POST /alerts/demo, POST /reset) and the same event types, so nothing that
consumes them has to change.
"""
from __future__ import annotations

import asyncio
import random
from collections import Counter
from typing import Any

from sqlalchemy import delete, update
from sqlmodel import select

from app.db import Database
from app.events import EventBus, EventType
from app.geo import WarningArea
from app.models import (
    URGENCY_STATUS,
    Action,
    ActionStatus,
    ActionType,
    Call,
    CallPolicy,
    HiddenTruth,
    Household,
    Need,
    Status,
    Urgency,
)
from app.risk import call_order, wave_for

LINES = 40  # calls at once
CALL_SECONDS = (60, 150)  # simulated length of one call
STATUS_EVERY_S = 600  # a status update every 10 simulated minutes


def reset_run(db: Database) -> None:
    """Everything back to not called; actions, needs and calls cleared. Events are kept (the log)."""
    with db.session() as session:
        session.execute(delete(Need))
        session.execute(delete(Action))
        session.execute(delete(Call))
        session.execute(update(Household).values(status=Status.NOT_CALLED, wave=None))
        session.commit()


def needs_reset(db: Database) -> bool:
    with db.session() as session:
        return session.exec(select(Household.id).where(Household.status != Status.NOT_CALLED).limit(1)).first() is not None


async def run(db: Database, bus: EventBus, area: WarningArea, speed: float) -> None:
    rng = random.Random(7)
    with db.session() as session:
        households = session.exec(select(Household)).all()
        truths = {t.household_id: t for t in session.exec(select(HiddenTruth)).all()}
    affected = [h for h in households if area.contains(h.lat, h.lng)]
    ranked = call_order(affected, CallPolicy.RISK_RANKED)
    waves = Counter()
    with db.session() as session:
        for h in ranked:
            h.wave = wave_for(h.risk_score)
            waves[h.wave] += 1
            session.add(h)
        session.commit()

    bus.emit(EventType.ALERT_RECEIVED, {
        "title": area.title, "source": "saved", "polygon": area.feature["geometry"],
        "affected_ids": [h.id for h in affected],
        "text": f"{area.title} received: {len(affected):,} homes inside the warning area.",
    })
    summary = f"{waves[1]} high-risk homes first, in 3 waves."
    bus.emit(EventType.PLAN_CREATED, {
        "summary": summary, "affected": len(affected), "waves": {str(w): waves[w] for w in (1, 2, 3)},
        "text": f"Plan: {summary} (stand-in engine until M3)",
    })

    tally: Counter[str] = Counter()
    queue: asyncio.Queue[Household] = asyncio.Queue()
    for h in ranked:
        if h.id in truths:  # live demo homes wait for a real call (M6)
            queue.put_nowait(h)

    async def line() -> None:
        while True:
            try:
                h = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            _set_status(db, bus, h, Status.CALLING)
            bus.emit(EventType.CALL_STARTED, {"call_id": None, "channel": "scripted", "attempt": 1}, h.id)
            await asyncio.sleep(rng.uniform(*CALL_SECONDS) / speed)
            _finish_call(db, bus, h, truths[h.id], tally)

    async def status_updates() -> None:
        while True:
            await asyncio.sleep(STATUS_EVERY_S / speed)
            bus.emit(EventType.STATUS_UPDATE, {
                "counts": dict(tally),
                "text": (f"Status: {tally['called']:,} called, {tally['reached']:,} reached, "
                         f"{tally['urgent']} urgent, {tally['help']} sent help, "
                         f"{queue.qsize():,} still to call."),
            })

    ticker = asyncio.create_task(status_updates())
    try:
        await asyncio.gather(*(line() for _ in range(LINES)))
    finally:
        ticker.cancel()
    bus.emit(EventType.AGENT_NOTE, {
        "text": (f"First pass done: {tally['reached']:,} reached, {tally['urgent']} urgent, "
                 f"{tally['help']} sent help, {tally['no_answer']} didn't answer (follow-ups come in M8)."),
    })


def _set_status(db: Database, bus: EventBus, h: Household, status: Status) -> None:
    previous = h.status
    h.status = status
    with db.session() as session:
        session.execute(update(Household).where(Household.id == h.id).values(status=status))
        session.commit()
    bus.emit(EventType.HOUSEHOLD_STATUS, {"status": status, "previous": previous}, h.id)


def _add_action(db: Database, bus: EventBus, h: Household, kind: ActionType, reason: str, text: str | None) -> None:
    with db.session() as session:
        action = Action(household_id=h.id, type=kind, status=ActionStatus.SCHEDULED, reason=reason)
        session.add(action)
        session.commit()
        action_id = action.id
    payload: dict[str, Any] = {"action_id": action_id, "type": kind, "status": ActionStatus.SCHEDULED,
                               "resource_id": None, "reason": reason}
    if text:
        payload["text"] = text
    bus.emit(EventType.ACTION_CREATED, payload, h.id)


def _finish_call(db: Database, bus: EventBus, h: Household, truth: HiddenTruth, tally: Counter) -> None:
    tally["called"] += 1
    if truth.answers_on_attempt != 1:
        _set_status(db, bus, h, Status.NO_ANSWER)
        tally["no_answer"] += 1
        return
    tally["reached"] += 1
    status = URGENCY_STATUS[truth.true_urgency]
    _set_status(db, bus, h, status)
    street = h.address.split(",")[0]
    category, _, detail = (truth.true_needs[0] if truth.true_needs else "other:").partition(":")
    if truth.true_urgency is Urgency.P1:
        tally["urgent"] += 1
        tally["help"] += 1
        _add_action(db, bus, h, ActionType.TRANSFER_TO_HUMAN, detail,
                    f"Emergency at {street}: {detail}. Told them to call 911 and handed off to a dispatcher.")
    elif truth.true_urgency is Urgency.P2:
        tally["urgent"] += 1
        tally["help"] += 1
        crew = f"Crew {tally['help'] % 6 + 1}"
        _add_action(db, bus, h, ActionType.DISPATCH_CREW, detail, f"Sent {crew} to {street}: {detail}.")
    elif truth.true_urgency is Urgency.P3:
        tally["help"] += 1
        if category in ("supplies", "power", "medical"):
            _add_action(db, bus, h, ActionType.DISPATCH_CREW, detail, f"Sent a crew to {street}: {detail}.")
        else:
            adjuster = f"Adjuster {tally['help'] % 4 + 1}"
            _add_action(db, bus, h, ActionType.SCHEDULE_ADJUSTER, detail,
                        f"Booked {adjuster} for {street}: {detail}. Claim opened.")
            _add_action(db, bus, h, ActionType.OPEN_CLAIM, detail, None)
