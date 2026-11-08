"""ReplayChannel: stored simulated calls (M7) played back through the engine on a simulated clock.

Each household's stored conversation is what happens when it picks up. Whether it picks up on a
given attempt comes from the hidden truth (answers_on_attempt), so the follow-up ladder works the same
as with scripted calls. Households with no stored conversation fall back to the scripted channel, and
the evaluation reports how many did.
"""
from __future__ import annotations

import random
from dataclasses import replace

from app.clock import Clock
from app.engine.channels import CallResult, DoneCallback, ScriptedChannel
from app.models import CallOutcome, Channel, HiddenTruth, Household


class ReplayChannel:
    name = Channel.REPLAY

    def __init__(self, clock: Clock, outcomes: dict[int, CallResult], truths: dict[int, HiddenTruth], seed: int = 42):
        self.clock = clock
        self.outcomes = outcomes
        self.truths = truths
        self.seed = seed
        self.fallback = ScriptedChannel(clock, truths, seed)
        self.replayed: set[int] = set()
        self.scripted: set[int] = set()

    def start(self, household: Household, attempt: int, done: DoneCallback) -> None:
        result = self.result_for(household, attempt)
        self.clock.call_later(result.duration_s, lambda: done(result))

    def result_for(self, h: Household, attempt: int) -> CallResult:
        stored = self.outcomes.get(h.id)
        if stored is None:
            self.scripted.add(h.id)
            return self.fallback.result_for(h, attempt)
        self.replayed.add(h.id)
        truth = self.truths.get(h.id)
        answers_on = truth.answers_on_attempt if truth else 1
        if stored.outcome is CallOutcome.NO_ANSWER or answers_on is None or attempt < answers_on:
            rng = random.Random(f"{self.seed}:{h.id}:{attempt}:replay")
            return CallResult(h.id, attempt, self.name, CallOutcome.NO_ANSWER, duration_s=rng.uniform(25, 40),
                              summary="No answer.")
        return replace(stored, attempt=attempt, channel=self.name, duration_s=max(stored.duration_s, 20.0))

    def text_reply(self, h: Household) -> tuple[float, str] | None:
        return self.fallback.text_reply(h)
