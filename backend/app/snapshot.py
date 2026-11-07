"""The /state snapshot and the shared counter rules (docs/API.md, "Counters")."""
from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.db import Database, iso_utc, utcnow
from app.events import EventBus, EventType
from app.models import (
    HELP_ACTIONS,
    NOT_REACHED,
    REACHED,
    Action,
    ActionStatus,
    Event,
    Household,
    Resource,
    Status,
)

URGENT = frozenset({Status.URGENT, Status.EMERGENCY})


def current_alert(db: Database) -> dict[str, Any] | None:
    """The latest alert.received event since the last reset, or None."""
    with db.session() as session:
        reset = session.exec(
            select(Event.id).where(Event.type == EventType.RUN_RESET.value).order_by(Event.id.desc()).limit(1)
        ).first()
        alert = session.exec(
            select(Event)
            .where(Event.type == EventType.ALERT_RECEIVED.value, Event.id > (reset or 0))
            .order_by(Event.id.desc())
            .limit(1)
        ).first()
    return alert.to_wire() if alert else None


def helped_household_ids(db: Database) -> list[int]:
    with db.session() as session:
        rows = session.exec(
            select(Action.household_id)
            .where(Action.type.in_(list(HELP_ACTIONS)))
            .where(Action.status.not_in([ActionStatus.REJECTED, ActionStatus.CANCELLED]))
            .distinct()
        ).all()
    return sorted(rows)


def compute_counts(rows: list[dict[str, Any]], helped: list[int]) -> dict[str, int]:
    affected = [r for r in rows if r["affected"]]
    return {
        "households": len(rows),
        "affected": len(affected),
        "called": sum(r["status"] != Status.NOT_CALLED for r in affected),
        "reached": sum(r["status"] in REACHED for r in rows),
        "urgent": sum(r["status"] in URGENT for r in rows),
        "help_sent": len(helped),
        "not_reached": sum(r["status"] in NOT_REACHED for r in affected),
    }


def build_state(db: Database, bus: EventBus) -> dict[str, Any]:
    last_event_id = bus.last_id()  # read first, so the client's catch-up can only overlap, not miss
    alert = current_alert(db)
    affected_ids = set(alert["payload"].get("affected_ids", [])) if alert else set()
    with db.session() as session:
        households = session.exec(select(Household).order_by(Household.id)).all()
        resources = session.exec(select(Resource).order_by(Resource.id)).all()
    helped = helped_household_ids(db)
    rows = [
        {
            "id": h.id, "name": h.name, "address": h.address, "lat": h.lat, "lng": h.lng,
            "status": h.status.value, "risk_score": h.risk_score, "wave": h.wave,
            "affected": h.id in affected_ids, "live": h.live,
        }
        for h in households
    ]
    warning_area = None
    if alert:
        warning_area = {
            "type": "Feature",
            "properties": {
                "title": alert["payload"].get("title", "Warning"),
                "issued_at": alert["time"],
                "event_id": alert["id"],
            },
            "geometry": alert["payload"]["polygon"],
        }
    return {
        "time": iso_utc(utcnow()),
        "last_event_id": last_event_id,
        "warning_area": warning_area,
        "households": rows,
        "resources": [
            {"id": r.id, "type": r.type.value, "name": r.name, "lat": r.lat, "lng": r.lng,
             "capacity": r.capacity, "used": r.used}
            for r in resources
        ],
        "helped_household_ids": helped,
        "counts": compute_counts(rows, helped),
    }
