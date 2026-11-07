"""Health, plus the shared vocabulary (statuses, colors, event types) the dashboard reads."""
from typing import Any

from fastapi import APIRouter, Request

from app.db import iso_utc, utcnow
from app.events import EVENT_DOCS
from app.models import (
    COVERED,
    REACHED,
    STATUS_STYLE,
    URGENCY_LABEL,
    URGENCY_STATUS,
    ActionType,
    ResourceType,
    Status,
    Urgency,
)

router = APIRouter(tags=["system"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {
        "status": "ok",
        "time": iso_utc(utcnow()),
        "db": state.db.path.name,
        "last_event_id": state.bus.last_id(),
        "llm": {
            "nvidia_keys": len(state.settings.nvidia_keys),
            "openrouter": bool(state.settings.openrouter_api_key),
        },
    }


@router.get("/meta")
def meta(request: Request) -> dict[str, Any]:
    return {
        "display_tz": request.app.state.settings.display_tz,
        "statuses": [
            {"value": s.value, **STATUS_STYLE[s], "reached": s in REACHED, "covered": s in COVERED}
            for s in Status
        ],
        "urgencies": [
            {
                "value": u.value,
                "label": URGENCY_LABEL[u],
                "status": URGENCY_STATUS[u].value,
                "color": STATUS_STYLE[URGENCY_STATUS[u]]["color"],
            }
            for u in Urgency
        ],
        "event_types": [{"value": t.value, "doc": doc} for t, doc in EVENT_DOCS.items()],
        "resource_types": [r.value for r in ResourceType],
        "action_types": [a.value for a in ActionType],
    }
