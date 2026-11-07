"""Backend handlers for the voice spec's in-call tools.

The phone platform delivers each tool call to POST /vapi/webhook, which calls run_tool(). A handler
returns the plain-words string the agent gets back as the tool result, and puts a line in the agent
feed (an event with a `text` field).

M1: record_need is real. It stores a Need row when the call's household exists and always emits
need.recorded. The other four are stubs that log a feed line and tell the agent what to say;
M6 wires them to status changes, crew booking, the dispatcher transfer and real texts.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.db import Database
from app.events import EventBus, EventType
from app.models import URGENCY_LABEL, Household, Need, NeedCategory, Urgency

log = logging.getLogger(__name__)


class UnknownTool(KeyError):
    """The agent called a tool that isn't in the voice spec."""


@dataclass
class ToolContext:
    db: Database
    bus: EventBus
    household_id: int | None = None  # from the call's metadata; absent on M1 test calls
    call_id: str | None = None  # the phone platform's call id
    caller_name: str | None = None  # the {{name}} the call was placed with


def run_tool(name: str, arguments: Any, ctx: ToolContext) -> str:
    """Run one tool call. `arguments` may be a dict or a JSON string (the OpenAI wire format)."""
    handler = HANDLERS.get(name)
    if handler is None:
        raise UnknownTool(name)
    return handler(ctx, parse_arguments(arguments))


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("tool arguments are not JSON: %.200s", raw)
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def parse_urgency(value: Any, default: Urgency = Urgency.P3) -> Urgency:
    """Accepts "P2", "p2", "2", "P2 urgent" or a label like "urgent"."""
    text = str(value or "").strip().upper()
    digit = re.search(r"[1-4]", text)
    if digit:
        return Urgency(f"P{digit.group()}")
    for word, level in (("EMERGENCY", Urgency.P1), ("URGENT", Urgency.P2), ("HELP", Urgency.P3), ("SAFE", Urgency.P4)):
        if word in text:
            return level
    if text:
        log.warning("unknown urgency %r; using %s", value, default.value)
    return default


def parse_category(value: Any) -> NeedCategory:
    text = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    try:
        return NeedCategory(text)
    except ValueError:
        if text:
            log.warning("unknown need category %r; using other", value)
        return NeedCategory.OTHER


def find_household(db: Database, household_id: int | None) -> Household | None:
    if household_id is None:
        return None
    with db.session() as session:
        return session.get(Household, household_id)


# --- Handlers -----------------------------------------------------------------------------


def record_need(ctx: ToolContext, args: dict[str, Any]) -> str:
    category = parse_category(args.get("category"))
    urgency = parse_urgency(args.get("urgency"))
    details = _clean(args.get("details")).rstrip(" .") or "no details given"
    household = find_household(ctx.db, ctx.household_id)
    need_id = None
    if household is not None:
        with ctx.db.session() as session:
            need = Need(household_id=household.id, category=category, details=details, urgency=urgency)
            session.add(need)
            session.commit()
            need_id = need.id
    text = (
        f"Recorded a need for {_who(ctx, household)}: {details}"
        f" ({category.value.replace('_', ' ')}, {urgency.value} {URGENCY_LABEL[urgency].lower()})."
    )
    ctx.bus.emit(
        EventType.NEED_RECORDED,
        {
            "category": category,
            "details": details,
            "urgency": urgency,
            "text": text,
            "call_id": ctx.call_id,
            "need_id": need_id,
        },
        household_id=household.id if household else None,
    )
    if urgency is Urgency.P1:
        return (
            "Need saved as an emergency. If you haven't yet: tell them to hang up and call 911,"
            " call flag_urgent with P1, then transfer_to_human."
        )
    if urgency is Urgency.P2:
        return "Need saved as urgent. If you haven't yet, call flag_urgent with P2, then carry on."
    return "Need saved. Go on to the next unanswered question."


def flag_urgent(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Stub (M6 changes the status right away and books the nearest crew for P2)."""
    level = parse_urgency(args.get("level"), default=Urgency.P2)
    if level not in (Urgency.P1, Urgency.P2):
        level = Urgency.P2
    reason = _clean(args.get("reason")) or "no reason given"
    household = find_household(ctx.db, ctx.household_id)
    _note(
        ctx,
        household,
        f"Flagged {_who(ctx, household)} as {level.value} {URGENCY_LABEL[level].lower()}: {reason}.",
        tool="flag_urgent",
        level=level,
        reason=reason,
    )
    if level is Urgency.P1:
        return (
            "Flagged as an emergency for the response team. Make sure they've been told to hang up"
            " and call 911, then transfer them to a person."
        )
    return (
        "Flagged as urgent for the response team. No crew is booked yet, so say the team will"
        " arrange help and follow up soon, then carry on."
    )


def transfer_to_human(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Stub (M6 transfers the call to the dispatcher's phone and shows a handoff card)."""
    reason = _clean(args.get("reason")) or "no reason given"
    household = find_household(ctx.db, ctx.household_id)
    _note(
        ctx,
        household,
        f"Transfer to a person requested for {_who(ctx, household)}: {reason}.",
        tool="transfer_to_human",
        reason=reason,
    )
    return (
        "Live transfer isn't switched on yet. Tell them a person from our team will call them back"
        " shortly, and that if anyone is in danger they should hang up and call 911 now."
    )


def send_text(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Stub (M6 sends a real text, only to allowlisted numbers)."""
    message = _clean(args.get("message"), limit=320)
    household = find_household(ctx.db, ctx.household_id)
    _note(
        ctx,
        household,
        f'Text to {_who(ctx, household)} not sent (texting isn\'t switched on yet): "{message}"',
        tool="send_text",
        message=message,
    )
    return (
        "Texting isn't switched on yet, so no text was sent. Don't promise one; give the next step"
        " out loud instead."
    )


def end_call(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Stub (M6 hands the summary to the manager, which arranges the rest)."""
    summary = _clean(args.get("summary"), limit=500) or "no summary given"
    household = find_household(ctx.db, ctx.household_id)
    _note(
        ctx,
        household,
        f"Finished the call with {_who(ctx, household)}: {summary}",
        tool="end_call",
        summary=summary,
    )
    return 'Summary saved. Now say "Stay safe. Goodbye."'


HANDLERS: dict[str, Callable[[ToolContext, dict[str, Any]], str]] = {
    "record_need": record_need,
    "flag_urgent": flag_urgent,
    "transfer_to_human": transfer_to_human,
    "send_text": send_text,
    "end_call": end_call,
}


# --- helpers ---------------------------------------------------------------------------


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def _who(ctx: ToolContext, household: Household | None) -> str:
    if household is not None:
        return f"{household.name}, {household.address}"
    if ctx.caller_name:
        return f"{ctx.caller_name} (test call)"
    return "a test caller"


def _note(ctx: ToolContext, household: Household | None, text: str, **fields: Any) -> None:
    ctx.bus.emit(
        EventType.AGENT_NOTE,
        {"text": text, "call_id": ctx.call_id, **fields},
        household_id=household.id if household else None,
    )
