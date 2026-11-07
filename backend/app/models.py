"""The shared data contract: tables, statuses and enums, defined once.

The dashboard reads statuses, colors and event types from GET /meta instead of copying them.
Timestamps are timezone-aware UTC (app.db.utcnow); SQLModel stores and returns them as UTC.
JSON columns only save when you assign a new value; in-place edits such as list.append are not
tracked.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.db import iso_utc, utcnow

# --- Enums --------------------------------------------------------------------------


class Status(StrEnum):
    """A household's state on the map."""

    NOT_CALLED = "not_called"
    CALLING = "calling"
    SAFE = "safe"
    NEEDS_HELP = "needs_help"
    URGENT = "urgent"
    EMERGENCY = "emergency"
    NO_ANSWER = "no_answer"
    ESCALATED = "escalated"


class Urgency(StrEnum):
    P1 = "P1"  # Emergency: life in danger now
    P2 = "P2"  # Urgent: needs help within hours
    P3 = "P3"  # Needs help within a day
    P4 = "P4"  # Safe: no needs


class Persona(StrEnum):
    """How a simulated resident behaves on the phone (the PRD's styles plus the trap set)."""

    NORMAL = "normal"
    DOWNPLAYS = "downplays"
    HARD_OF_HEARING = "hard_of_hearing"
    SPANISH_ONLY = "spanish_only"
    CONFUSED = "confused"
    ANGRY = "angry"
    CALL_DAUGHTER = "call_daughter"
    HANGS_UP = "hangs_up"
    NEVER_ANSWERS = "never_answers"


class ResourceType(StrEnum):
    CREW = "crew"
    SHELTER = "shelter"
    ADJUSTER = "adjuster"
    GENERATOR = "generator"


class Channel(StrEnum):
    """How a call was made. The manager never needs to know which one ran."""

    SCRIPTED = "scripted"
    VAPI = "vapi"
    SIM = "sim"
    REPLAY = "replay"
    SMS = "sms"


class CallOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    HUNG_UP = "hung_up"
    TRANSFERRED = "transferred"
    FAILED = "failed"


class NeedCategory(StrEnum):
    INJURY = "injury"
    MEDICAL = "medical"
    POWER = "power"
    HOME_DAMAGE = "home_damage"
    SHELTER = "shelter"
    EVACUATION = "evacuation"
    SUPPLIES = "supplies"
    CONTACT = "contact"  # e.g. "call my daughter instead"
    OTHER = "other"


class ActionType(StrEnum):
    DISPATCH_CREW = "dispatch_crew"
    RESERVE_SHELTER_BED = "reserve_shelter_bed"
    SCHEDULE_ADJUSTER = "schedule_adjuster"
    OPEN_CLAIM = "open_claim"
    DELIVER_GENERATOR = "deliver_generator"
    HOTEL_VOUCHER = "hotel_voucher"
    IN_PERSON_CHECK = "in_person_check"
    TRANSFER_TO_HUMAN = "transfer_to_human"
    SEND_TEXT = "send_text"
    SCHEDULE_FOLLOWUP = "schedule_followup"


class ActionStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RunMode(StrEnum):
    LIVE = "live"
    SIM = "sim"


class CallPolicy(StrEnum):
    RISK_RANKED = "risk_ranked"
    LIST_ORDER = "list_order"


# --- Map look and status groups -------------------------------------------------------

GRAY, GREEN, YELLOW, ORANGE, RED = "#9ca3af", "#22c55e", "#eab308", "#f97316", "#ef4444"

# The PRD's five colors. The gray states differ by style: solid (not called), pulse (call in
# progress), hollow (no answer yet), ring (escalated to an in-person check; drawn with a red ring).
STATUS_STYLE: dict[Status, dict[str, str]] = {
    Status.NOT_CALLED: {"label": "Not called", "color": GRAY, "style": "solid"},
    Status.CALLING: {"label": "Calling", "color": GRAY, "style": "pulse"},
    Status.SAFE: {"label": "Safe", "color": GREEN, "style": "solid"},
    Status.NEEDS_HELP: {"label": "Needs help", "color": YELLOW, "style": "solid"},
    Status.URGENT: {"label": "Urgent", "color": ORANGE, "style": "solid"},
    Status.EMERGENCY: {"label": "Emergency", "color": RED, "style": "solid"},
    Status.NO_ANSWER: {"label": "No answer", "color": GRAY, "style": "hollow"},
    Status.ESCALATED: {"label": "Escalated", "color": GRAY, "style": "ring"},
}

# Reached: someone answered. Covered: reached or escalated to a person (the PRD's coverage metric).
REACHED = frozenset({Status.SAFE, Status.NEEDS_HELP, Status.URGENT, Status.EMERGENCY})
COVERED = REACHED | {Status.ESCALATED}
NOT_REACHED = frozenset({Status.NOT_CALLED, Status.CALLING, Status.NO_ANSWER})

# Actions that count toward the dashboard's "help sent" counter (texts, claims and follow-ups don't).
HELP_ACTIONS = frozenset({
    ActionType.DISPATCH_CREW,
    ActionType.RESERVE_SHELTER_BED,
    ActionType.SCHEDULE_ADJUSTER,
    ActionType.DELIVER_GENERATOR,
    ActionType.HOTEL_VOUCHER,
    ActionType.IN_PERSON_CHECK,
    ActionType.TRANSFER_TO_HUMAN,
})

URGENCY_STATUS = {
    Urgency.P1: Status.EMERGENCY,
    Urgency.P2: Status.URGENT,
    Urgency.P3: Status.NEEDS_HELP,
    Urgency.P4: Status.SAFE,
}
URGENCY_LABEL = {
    Urgency.P1: "Emergency",
    Urgency.P2: "Urgent",
    Urgency.P3: "Needs help",
    Urgency.P4: "Safe",
}


# --- Column helpers -------------------------------------------------------------------


def enum_column(enum_cls: type[StrEnum], *, index: bool = False, nullable: bool = False) -> Column:
    """Store an enum by its value ("not_called"), not by its Python name."""
    return Column(
        SAEnum(
            enum_cls,
            values_callable=lambda members: [m.value for m in members],
            native_enum=False,
            validate_strings=True,
            length=32,
        ),
        index=index,
        nullable=nullable,
    )


def json_column() -> Column:
    return Column(JSON, nullable=False)


# --- Tables: the PRD's seven, plus run_info and llm_usage --------------------------------


class Household(SQLModel, table=True):
    __tablename__ = "households"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone: str
    address: str
    lat: float
    lng: float
    age: int
    lives_alone: bool = False
    medical_device: Optional[str] = None  # one that needs power, e.g. "oxygen concentrator"
    mobility: Optional[str] = None  # e.g. "wheelchair"
    has_car: bool = True
    kids_under_5: int = 0
    language: str = "en"
    risk_score: float = 0.0
    risk_factors: dict[str, Any] = Field(default_factory=dict, sa_column=json_column())
    wave: Optional[int] = None
    status: Status = Field(default=Status.NOT_CALLED, sa_column=enum_column(Status, index=True))
    live: bool = False  # a consenting real phone (demo only); nothing else is ever dialed or texted


class HiddenTruth(SQLModel, table=True):
    """Test only: what is really going on in a fake household. The agent never sees it."""

    __tablename__ = "hidden_truth"

    household_id: int = Field(primary_key=True, foreign_key="households.id")
    true_urgency: Urgency = Field(sa_column=enum_column(Urgency))
    true_needs: list[str] = Field(default_factory=list, sa_column=json_column())
    persona_style: Persona = Field(default=Persona.NORMAL, sa_column=enum_column(Persona))
    # None: never answers. No non-null default: a default here turns an explicit None into 1 on save.
    answers_on_attempt: Optional[int] = None
    replies_to_text: bool = True


class Call(SQLModel, table=True):
    __tablename__ = "calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    channel: Channel = Field(sa_column=enum_column(Channel))
    attempt: int = 1
    external_id: Optional[str] = Field(default=None, index=True)  # e.g. the Vapi call id
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    outcome: Optional[CallOutcome] = Field(
        default=None, sa_column=enum_column(CallOutcome, nullable=True)
    )
    transcript: list[dict[str, Any]] = Field(default_factory=list, sa_column=json_column())
    summary: Optional[str] = None


class Need(SQLModel, table=True):
    __tablename__ = "needs"

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    call_id: Optional[int] = Field(default=None, foreign_key="calls.id")
    category: NeedCategory = Field(sa_column=enum_column(NeedCategory))
    details: str = ""
    urgency: Urgency = Field(sa_column=enum_column(Urgency))
    created_at: datetime = Field(default_factory=utcnow)


class Resource(SQLModel, table=True):
    __tablename__ = "resources"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: ResourceType = Field(sa_column=enum_column(ResourceType, index=True))
    name: str
    lat: float
    lng: float
    capacity: int = 1  # beds for a shelter; 1 for a crew, adjuster or generator
    used: int = 0
    free_at: Optional[datetime] = None  # when a busy crew, adjuster or generator frees up


class Action(SQLModel, table=True):
    __tablename__ = "actions"

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="households.id", index=True)
    type: ActionType = Field(sa_column=enum_column(ActionType))
    status: ActionStatus = Field(
        default=ActionStatus.SCHEDULED, sa_column=enum_column(ActionStatus, index=True)
    )
    resource_id: Optional[int] = Field(default=None, foreign_key="resources.id")
    cost: float = 0.0
    needs_approval: bool = False
    approved_by: Optional[str] = None
    reason: str = ""  # every decision is logged with its reason
    details: dict[str, Any] = Field(default_factory=dict, sa_column=json_column())
    scheduled_for: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    time: datetime = Field(default_factory=utcnow, index=True)  # the run's clock
    type: str = Field(index=True)
    household_id: Optional[int] = Field(default=None, index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=json_column())

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "time": iso_utc(self.time),
            "type": self.type,
            "household_id": self.household_id,
            "payload": self.payload,
        }


class RunInfo(SQLModel, table=True):
    __tablename__ = "run_info"

    id: int = Field(default=1, primary_key=True)
    mode: RunMode = Field(default=RunMode.LIVE, sa_column=enum_column(RunMode))
    policy: CallPolicy = Field(default=CallPolicy.RISK_RANKED, sa_column=enum_column(CallPolicy))
    lines: int = 100
    seed: int = 42
    created_at: datetime = Field(default_factory=utcnow)
    notes: str = ""


class LLMUsage(SQLModel, table=True):
    """Tokens and timing per model call: the cost metric, and reply delay on live calls."""

    __tablename__ = "llm_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)
    job: str
    provider: str
    model: str
    household_id: Optional[int] = Field(default=None, index=True)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: Optional[float] = None
    latency_ms: float = 0.0
    cached: bool = False
