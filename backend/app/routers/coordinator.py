"""Coordinator endpoints (M8): the approvals queue, one household's details, and inbound texts."""
import re
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, HTTPException, Request, Response
from sqlmodel import select

from app.db import iso_utc, utcnow
from app.events import EventType
from app.models import Action, ActionStatus, Call, Household, Need, Resource, Status
from app.risk import FACTOR_LABELS
from app.snapshot import current_alert

router = APIRouter(tags=["coordinator"])
LIFE_SAFETY = {Status.EMERGENCY, Status.URGENT}


def _approval_item(action: Action, h: Household) -> dict[str, Any]:
    return {
        "action_id": action.id, "household_id": h.id, "name": h.name, "address": h.address,
        "type": action.type.value, "cost": action.cost, "reason": action.reason, "status": action.status.value,
        "urgency_status": h.status.value, "created_at": iso_utc(action.created_at),
    }


@router.get("/approvals")
def list_approvals(request: Request) -> dict[str, Any]:
    db = request.app.state.db
    with db.session() as session:
        rows = session.exec(
            select(Action, Household).join(Household, Household.id == Action.household_id)
            .where(Action.status == ActionStatus.PENDING_APPROVAL)
        ).all()
    items = [_approval_item(a, h) for a, h in rows]
    items.sort(key=lambda i: (i["urgency_status"] not in LIFE_SAFETY, i["created_at"] or "", i["action_id"]))
    return {"approvals": items}


def _decide(request: Request, action_id: int, approved: bool, by: str) -> dict[str, Any]:
    state = request.app.state
    engine = state.live_run.engine
    db = state.db
    if engine is not None:
        try:
            action = engine.decide_approval(action_id, approved, by)
        except KeyError:
            raise HTTPException(404, "no such action") from None
        except ValueError:
            raise HTTPException(409, "already decided") from None
    else:  # no run in memory (say, after a restart): record the decision only
        with db.session() as session:
            action = session.get(Action, action_id)
            if action is None:
                raise HTTPException(404, "no such action")
            if action.status is not ActionStatus.PENDING_APPROVAL:
                raise HTTPException(409, "already decided")
            action.status = ActionStatus.SCHEDULED if approved else ActionStatus.REJECTED
            action.approved_by, action.updated_at = by, utcnow()
            session.add(action)
            session.commit()
        state.bus.emit(EventType.APPROVAL_DECIDED, {
            "action_id": action_id, "type": action.type, "approved": approved, "by": by,
            "text": f"{'Approved' if approved else 'Rejected'} by the {by} (no run active, recorded only).",
        }, action.household_id)
    with db.session() as session:
        h = session.get(Household, action.household_id)
    return _approval_item(action, h)


@router.post("/approvals/{action_id}/approve")
async def approve(request: Request, action_id: int, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return _decide(request, action_id, True, str((body or {}).get("by") or "coordinator"))


@router.post("/approvals/{action_id}/reject")
async def reject(request: Request, action_id: int, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return _decide(request, action_id, False, str((body or {}).get("by") or "coordinator"))


@router.get("/households/{household_id}")
def household_detail(request: Request, household_id: int) -> dict[str, Any]:
    state = request.app.state
    with state.db.session() as session:
        h = session.get(Household, household_id)
        if h is None:
            raise HTTPException(404, "no such household")
        needs = session.exec(select(Need).where(Need.household_id == household_id).order_by(Need.id)).all()
        calls = session.exec(select(Call).where(Call.household_id == household_id).order_by(Call.id)).all()
        actions = session.exec(select(Action).where(Action.household_id == household_id).order_by(Action.id)).all()
        names = {r.id: r.name for r in session.exec(select(Resource)).all()}
    alert = current_alert(state.db)
    affected = bool(alert and household_id in set(alert["payload"].get("affected_ids", [])))
    engine = state.live_run.engine
    next_followup = None
    if engine is not None:
        step = engine.next_step.get(household_id)
        if step is not None:
            next_followup = dict(step)
        elif household_id in engine.queue:
            next_followup = {"step": 3 if engine.attempts[household_id] >= 2 else 1, "channel": "call", "at": iso_utc(engine.clock.now())}
    return {
        "id": h.id, "name": h.name, "address": h.address, "lat": h.lat, "lng": h.lng, "status": h.status.value,
        "live": h.live, "affected": affected, "risk_score": h.risk_score, "wave": h.wave,
        "risk_factors": [{"key": k, "label": FACTOR_LABELS.get(k, k), "points": v}
                         for k, v in sorted(h.risk_factors.items(), key=lambda kv: -kv[1])],
        "profile": {"age": h.age, "lives_alone": h.lives_alone, "medical_device": h.medical_device,
                    "mobility": h.mobility, "has_car": h.has_car, "kids_under_5": h.kids_under_5,
                    "language": h.language},
        "needs": [{"id": n.id, "category": n.category.value, "details": n.details, "urgency": n.urgency.value,
                   "created_at": iso_utc(n.created_at)} for n in needs],
        "calls": [{"id": c.id, "attempt": c.attempt, "channel": c.channel.value,
                   "outcome": c.outcome.value if c.outcome else None, "started_at": iso_utc(c.started_at),
                   "ended_at": iso_utc(c.ended_at), "summary": c.summary, "transcript": c.transcript} for c in calls],
        "actions": [{"id": a.id, "type": a.type.value, "status": a.status.value, "cost": a.cost, "reason": a.reason,
                     "needs_approval": a.needs_approval, "resource": names.get(a.resource_id),
                     "scheduled_for": iso_utc(a.scheduled_for), "created_at": iso_utc(a.created_at)} for a in actions],
        "next_followup": next_followup,
    }


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-10:]


@router.post("/sms/inbound")
async def sms_inbound(request: Request) -> Response:
    """Twilio posts inbound texts here (form fields From, Body). Matches the sender to a household."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    From, Body = form.get("From", [""])[0], form.get("Body", [""])[0]  # noqa: N806 (Twilio's names)
    state = request.app.state
    digits = _digits(From)
    household = None
    if digits:
        with state.db.session() as session:
            for h in session.exec(select(Household)).all():
                if _digits(h.phone) == digits:
                    household = h
                    break
    engine = state.live_run.engine
    if household is not None and engine is not None and household.id in engine.households:
        engine.handle_text_reply(engine.households[household.id], Body)
    elif household is not None:
        state.bus.emit(EventType.SMS_RECEIVED, {"body": Body[:300], "parsed": "no_run",
                                                "text": f"Text from {household.address.split(',')[0]} (no run active): \"{Body[:80]}\""},
                       household.id)
    # An empty TwiML reply: Twilio sends nothing back to the person.
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")
