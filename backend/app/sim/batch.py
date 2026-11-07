"""Batch simulation: pick households, run their calls with a concurrency cap, store each result
as it finishes (so a crash or Ctrl+C loses at most the calls in flight), and skip households
already stored for the current spec and models (so a rerun resumes).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from app.db import Database
from app.geo import WarningArea
from app.llm import LLMClient, LLMError
from app.models import HiddenTruth, Household, Persona, RunInfo, Urgency
from app.sim.conversation import SimRun, simulate_call
from app.sim.store import OutcomeStore, SimKey, StoredOutcome

log = logging.getLogger(__name__)

TRAP_PERSONAS = frozenset(Persona) - {Persona.NORMAL, Persona.NEVER_ANSWERS}
URGENT = (Urgency.P1, Urgency.P2)
RETRY_BACKOFF_S = (20.0, 60.0)  # waits before the 2nd and 3rd try of a household whose call failed


@dataclass
class City:
    seed: int
    households: dict[int, Household]
    truths: dict[int, HiddenTruth]


def load_city(db_path: Path | str) -> City:
    db = Database(db_path)
    try:
        with db.session() as session:
            info = session.get(RunInfo, 1)
            households = {h.id: h for h in session.exec(select(Household)).all()}
            truths = {t.household_id: t for t in session.exec(select(HiddenTruth)).all()}
    finally:
        db.dispose()
    return City(info.seed if info else 42, households, truths)


def _urgent_first(city: City, ids: list[int]) -> list[int]:
    """True P1/P2, then traps, then the rest by risk score: a partial run is still useful."""
    def rank(i: int) -> tuple:
        t = city.truths[i]
        return (t.true_urgency not in URGENT, t.persona_style not in TRAP_PERSONAS,
                -city.households[i].risk_score, i)
    return sorted(ids, key=rank)


def select_households(
    city: City, tier: str, area: WarningArea | None = None, pilot_n: int = 200, sample_seed: int = 7
) -> list[int]:
    """Household ids for a tier: "traps", "pilot" or "all". Only households with a hidden truth
    (live demo households have none and are never simulated)."""
    simulated = [i for i in city.households if i in city.truths]
    if tier == "traps":
        return sorted(i for i in simulated if city.truths[i].persona_style in TRAP_PERSONAS)
    inside = [i for i in simulated if area is None or area.contains(city.households[i].lat, city.households[i].lng)]
    if tier == "all":
        return _urgent_first(city, inside)
    if tier == "pilot":
        urgent = [i for i in inside if city.truths[i].true_urgency in URGENT]
        rest = sorted(set(inside) - set(urgent))
        sample = random.Random(sample_seed).sample(rest, max(0, min(len(rest), pilot_n - len(urgent))))
        return _urgent_first(city, urgent + sample)[:pilot_n]
    raise ValueError(f"unknown tier {tier!r}")


@dataclass
class Progress:
    total: int
    skipped: int = 0  # already stored before this run
    done: int = 0
    failed: int = 0
    tokens: int = 0  # new (uncached) tokens spent this run
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def rate_per_min(self) -> float:
        return self.done / self.elapsed * 60 if self.elapsed > 0 else 0.0

    @property
    def eta_s(self) -> float | None:
        todo = self.total - self.skipped - self.done - self.failed
        return todo / (self.done / self.elapsed) if self.done and self.elapsed > 0 else None

    def line(self) -> str:
        eta = self.eta_s
        eta_text = "--" if eta is None else _clock(eta)
        finished = self.skipped + self.done
        return (f"{finished}/{self.total} stored ({self.skipped} already, {self.done} new,"
                f" {self.failed} failed) | {self.rate_per_min:.1f}/min | left {eta_text}"
                f" | {self.tokens:,} new tokens | {_clock(self.elapsed)}")


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


async def run_batch(
    llm: LLMClient,
    store: OutcomeStore,
    key: SimKey,
    city: City,
    ids: list[int],
    *,
    concurrency: int = 8,
    max_agent_turns: int | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    backoff: tuple[float, ...] = RETRY_BACKOFF_S,
) -> Progress:
    """Simulate every household in `ids` not already stored under `key`, storing each result."""
    stored = store.stored_ids(key)
    todo = [i for i in ids if i not in stored]
    progress = Progress(total=len(ids), skipped=len(ids) - len(todo))
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in todo:
        queue.put_nowait(i)
    if on_progress:
        on_progress(progress)
    kwargs = {} if max_agent_turns is None else {"max_agent_turns": max_agent_turns}

    async def one(household_id: int) -> SimRun | None:
        h, t = city.households[household_id], city.truths[household_id]
        for attempt in range(len(backoff) + 1):
            try:
                return await simulate_call(llm, h, t, **kwargs)
            except LLMError as exc:
                # Turns that already succeeded are in the LLM cache, so a retry resumes the call.
                log.warning("household %s: call failed (try %d): %s", household_id, attempt + 1, exc)
                if attempt < len(backoff):
                    await asyncio.sleep(backoff[attempt])
        return None

    async def worker() -> None:
        while True:
            try:
                household_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            run = await one(household_id)
            if run is None:
                progress.failed += 1
            else:
                store.save(key, run)
                progress.done += 1
                progress.tokens += run.new_tokens
            if on_progress:
                on_progress(progress)

    workers = [asyncio.create_task(worker()) for _ in range(max(1, min(concurrency, len(todo) or 1)))]
    try:
        await asyncio.gather(*workers)
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    return progress


# --- summary ---------------------------------------------------------------------------------


def summarize(city: City, outcomes: dict[int, StoredOutcome], ids: list[int]) -> str:
    rows = [(i, outcomes[i]) for i in ids if i in outcomes]
    if not rows:
        return "Nothing stored yet for these households."
    lines = [f"Stored calls: {len(rows)} of {len(ids)} households"]
    outcome_counts = Counter(o.result.outcome.value for _, o in rows)
    lines.append("Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcome_counts.items())))
    ended = Counter(o.ended_by for _, o in rows)
    lines.append("Ended by: " + ", ".join(f"{k} {v}" for k, v in sorted(ended.items())))

    found_cols = [u.value for u in Urgency] + ["none"]
    lines.append("")
    lines.append("Urgency found (columns) vs true urgency (rows):")
    lines.append("  true  " + "".join(f"{c:>6}" for c in found_cols))
    table: dict[str, Counter] = {u.value: Counter() for u in Urgency}
    for i, o in rows:
        found = o.result.urgency.value if o.result.urgency else "none"
        table[city.truths[i].true_urgency.value][found] += 1
    for true, counts in table.items():
        if sum(counts.values()):
            lines.append(f"  {true:>4}  " + "".join(f"{counts[c]:>6}" for c in found_cols))

    true_urgent = [i for i, _ in rows if city.truths[i].true_urgency in URGENT]
    flagged = [i for i, o in rows if o.result.urgency in URGENT]
    caught = [i for i in true_urgent if outcomes[i].result.urgency in URGENT]
    false_alarms = [i for i in flagged if city.truths[i].true_urgency not in URGENT]
    lines.append("")
    if true_urgent:
        lines.append(f"Urgent found: {len(caught)}/{len(true_urgent)} true P1+P2 flagged P1/P2"
                     f" ({100 * len(caught) / len(true_urgent):.0f}%)")
    if flagged:
        lines.append(f"False alarms: {len(false_alarms)}/{len(flagged)} flagged urgent were really P3/P4")
    safe_wrong = [i for i, o in rows if o.result.urgency is Urgency.P4
                  and city.truths[i].true_urgency is not Urgency.P4]
    lines.append(f"Marked safe but not safe: {len(safe_wrong)}")

    traps = [(i, o) for i, o in rows if city.truths[i].persona_style in TRAP_PERSONAS]
    if traps:
        by_persona: dict[str, Counter] = {}
        for i, o in traps:
            p = city.truths[i].persona_style.value
            by_persona.setdefault(p, Counter())[o.result.urgency.value if o.result.urgency else o.result.outcome.value] += 1
        lines.append("Traps (found urgency or outcome): " + "; ".join(
            f"{p}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())) for p, c in sorted(by_persona.items())))

    talked = [o for _, o in rows if o.model_calls]
    if talked:
        tokens = sum(o.total_tokens for o in talked)
        lines.append("")
        lines.append(f"Tokens per answered household: {tokens / len(talked):,.0f}"
                     f" (prompt {sum(o.prompt_tokens for o in talked) / len(talked):,.0f},"
                     f" completion {sum(o.completion_tokens for o in talked) / len(talked):,.0f});"
                     f" model calls per household: {sum(o.model_calls for o in talked) / len(talked):.1f}")
        lines.append(f"Tokens per household (all {len(rows)}): {tokens / len(rows):,.0f}")
        lines.append(f"Simulated call length: {sum(o.result.duration_s for o in talked) / len(talked) / 60:.1f} min average")
    return "\n".join(lines)


def format_transcript(household: Household | None, truth: HiddenTruth | None, key: SimKey,
                      stored: StoredOutcome) -> str:
    r = stored.result
    head = [f"Household {r.household_id}" + (f": {household.name}, {household.address}" if household else "")]
    if truth is not None:
        head.append(f"Persona: {truth.persona_style.value}; true urgency {truth.true_urgency.value};"
                    f" true needs: {'; '.join(truth.true_needs) or 'none'}")
    head.append(f"Spec {key.spec_hash}, voice {key.voice_model}, resident {key.resident_model}, seed {key.seed}")
    head.append(f"Outcome {r.outcome.value} (ended by {stored.ended_by}); found urgency"
                f" {r.urgency.value if r.urgency else 'none'}; {r.duration_s / 60:.1f} min;"
                f" {stored.total_tokens:,} tokens in {stored.model_calls} model calls")
    body = []
    turns: dict[int, list[dict]] = {}
    for call in stored.tool_calls:
        turns.setdefault(call.get("turn", 0), []).append(call)
    agent_turn = -1  # the opening line is turn 0, spoken before any tool call
    for entry in r.transcript:
        who = "AGENT   " if entry["role"] == "agent" else "RESIDENT"
        if entry["role"] == "agent":
            agent_turn += 1
            for call in turns.get(agent_turn, []):
                body.append(f"          [tool] {call['name']}({_args(call.get('arguments'))})")
        body.append(f"{who}: {entry['line']}")
    for turn in sorted(t for t in turns if t > agent_turn):  # e.g. a silent transfer at the end
        for call in turns[turn]:
            body.append(f"          [tool] {call['name']}({_args(call.get('arguments'))})")
    tail =[f"Needs: " + ("; ".join(f"{n.category.value} {n.urgency.value}: {n.details}" for n in r.needs) or "none"),
            f"Summary: {r.summary}", f"People: {r.people}"]
    return "\n".join(head + [""] + body + [""] + tail)


def _args(args: object) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v!r}" for k, v in args.items())
    return str(args)
