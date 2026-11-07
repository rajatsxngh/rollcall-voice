"""Call channels (design decision 2): the engine starts a call, and the channel reports the result
later through the `done` callback. The engine never knows which channel ran the call.

Channels: ScriptedChannel (here, M3: answers from the hidden truth, no AI), a phone channel (M6),
simulated residents (M7), and replay of stored simulated calls (M9).
"""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.clock import Clock
from app.models import CallOutcome, Channel, HiddenTruth, Household, NeedCategory, Persona, Urgency

REACHED_OUTCOMES = frozenset({CallOutcome.COMPLETED, CallOutcome.TRANSFERRED})


@dataclass
class NeedReport:
    category: NeedCategory
    details: str
    urgency: Urgency


@dataclass
class CallResult:
    """What one call found. Every channel produces exactly this."""

    household_id: int
    attempt: int
    channel: Channel
    outcome: CallOutcome
    urgency: Urgency | None = None  # what the call found; None when nobody was reached
    needs: list[NeedReport] = field(default_factory=list)
    summary: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)  # [{"role": "agent"|"resident", "line": str}]
    duration_s: float = 0.0
    people: int = 1  # people in the home, for shelter beds
    external_id: str | None = None  # e.g. the phone platform's call id

    @property
    def reached(self) -> bool:
        return self.outcome in REACHED_OUTCOMES


DoneCallback = Callable[[CallResult], Awaitable[None] | None]


class CallChannel(Protocol):
    name: Channel

    def start(self, household: Household, attempt: int, done: DoneCallback) -> None:
        """Begin a call. Call `done(result)` exactly once when it ends."""


def parse_need(text: str, urgency: Urgency) -> NeedReport:
    """Hidden-truth needs are stored as "category:details"."""
    category, _, details = text.partition(":")
    try:
        kind = NeedCategory(category)
    except ValueError:
        kind, details = NeedCategory.OTHER, text
    return NeedReport(kind, details.strip(), urgency)


class ScriptedChannel:
    """Answers straight from the hidden truth with realistic call lengths and no-answers.

    A stand-in for real and simulated calls, so the engine can be built and tested without AI. It is
    a perfect listener (it never misses a downplayed emergency), so it says nothing about how well
    the voice agent triages; the simulated residents in M7 measure that.
    """

    name = Channel.SCRIPTED

    def __init__(self, clock: Clock, truths: dict[int, HiddenTruth], seed: int = 42) -> None:
        self.clock = clock
        self.truths = truths
        self.seed = seed

    def start(self, household: Household, attempt: int, done: DoneCallback) -> None:
        result = self.result_for(household, attempt)
        self.clock.call_later(result.duration_s, lambda: done(result))

    def result_for(self, h: Household, attempt: int) -> CallResult:
        rng = random.Random(f"{self.seed}:{h.id}:{attempt}")  # same household + attempt, same call
        truth = self.truths.get(h.id)
        base = dict(household_id=h.id, attempt=attempt, channel=self.name)
        if truth is None or truth.answers_on_attempt is None or attempt < truth.answers_on_attempt:
            return CallResult(**base, outcome=CallOutcome.NO_ANSWER, duration_s=rng.uniform(25, 40),
                              summary="No answer.")
        if truth.persona_style is Persona.HANGS_UP:
            return CallResult(**base, outcome=CallOutcome.HUNG_UP, duration_s=rng.uniform(20, 60),
                              summary="Hung up partway through the call.")
        needs = [parse_need(n, truth.true_urgency) for n in truth.true_needs]
        first = h.name.split()[0]
        found = "; ".join(n.details for n in needs) if needs else "safe, no needs"
        people = 1 + (0 if h.lives_alone else rng.randint(1, 3)) + h.kids_under_5
        return CallResult(
            **base,
            outcome=CallOutcome.COMPLETED,
            urgency=truth.true_urgency,
            needs=needs,
            summary=f"Spoke with {first}: {found}.",
            duration_s=rng.uniform(60, 150),
            people=people,
        )
