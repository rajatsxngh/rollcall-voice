"""The manager agent (M5): Nemotron makes the response's judgment calls; plain code does the rest.

Four jobs, all on the manager model (Nemotron 3 Super):
- triage: from one call's result to the kinds of help the household needs (thinking off, fast);
- plan: the call plan stated in one line (thinking on);
- re-plan: when something changes (a new P1, every crew busy, shelters full), pick one change from a
  fixed menu; code applies it (thinking on);
- status: a short update for the coordinator every 10 run-minutes (thinking off).

Every model answer is validated, retried once, and otherwise replaced by the rule version (PRD risk
table: "validate, retry once, then fall back to a simple rule"). The triage and re-plan steps are
LangGraph graphs (ask -> check -> retry or fall back); the engine owns the loop and the timers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.engine.channels import CallResult
from app.engine.triage import Decision, HelpRequest, RuleTriage
from app.llm import LLMClient, LLMError, LLMResult, record_usage
from app.models import ActionType, Household, Urgency

log = logging.getLogger(__name__)

HELP_KINDS = {
    ActionType.TRANSFER_TO_HUMAN: "hand the call to a human dispatcher (P1 only; they were told to call 911)",
    ActionType.DISPATCH_CREW: "send the nearest crew for a wellness check, supplies or a device check",
    ActionType.RESERVE_SHELTER_BED: "hold shelter beds for everyone in the home",
    ActionType.SCHEDULE_ADJUSTER: "book an adjuster visit for home damage",
    ActionType.OPEN_CLAIM: "open a claim with what the caller said (pair it with an adjuster)",
    ActionType.DELIVER_GENERATOR: "deliver a generator for a medical device (waits for the coordinator's approval)",
    ActionType.HOTEL_VOUCHER: "a hotel voucher when they can't stay home (waits for approval)",
    ActionType.SCHEDULE_FOLLOWUP: "call someone back later, e.g. the relative they asked us to call",
}
NEEDS_APPROVAL = {ActionType.DELIVER_GENERATOR, ActionType.HOTEL_VOUCHER}
RANK = {Urgency.P1: 1, Urgency.P2: 2, Urgency.P3: 3, Urgency.P4: 4}

# The re-plan menu. Code validates and applies whatever the model picks.
REPLAN_CHANGES = {
    "none": "change nothing",
    "raise_priority_near": "call homes near a spot sooner: {lat, lng, radius_km (0.3 to 3)}",
    "switch_help": "use other help when one kind runs out: {from, to}; allowed: reserve_shelter_bed->hotel_voucher, deliver_generator->dispatch_crew",
    "change_lines": "change how many calls run at once: {lines (10 to 300)}",
    "flag_coordinator": "ask the human coordinator to look at something: {note}",
}
ALLOWED_SWITCHES = {("reserve_shelter_bed", "hotel_voucher"), ("deliver_generator", "dispatch_crew")}

TRIAGE_SYSTEM = (
    "You are the manager agent of RollCall, which phones every household in a tornado warning area for "
    "State Farm. You get one household's profile and what its check-in call found. Decide how urgent it "
    "is and which help to send. Urgency: P1 life in danger now; P2 needs help within hours (oxygen or "
    "CPAP on battery, older person alone with no power, home unlivable); P3 needs help within a day; P4 "
    "safe. Never rate it less urgent than the call did. Help kinds you may use:\n"
    + "\n".join(f"- {k.value}: {v}" for k, v in HELP_KINDS.items())
    + "\nSend only what the needs call for; P4 usually needs nothing. Reply with JSON only, no other text:\n"
    '{"urgency": "P2", "help": [{"kind": "dispatch_crew", "reason": "oxygen on battery, lives alone"}], '
    '"reason": "one short sentence"}\nKeep each reason under 12 words, in plain words.'
)


def _first_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply (models sometimes wrap it in prose or code fences)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clip(text: Any, words: int = 16) -> str:
    return " ".join(str(text or "").split()[:words]).strip(" .")


def household_profile(h: Household) -> str:
    bits = [f"age {h.age}", "lives alone" if h.lives_alone else "not alone"]
    if h.medical_device:
        bits.append(f"uses a {h.medical_device}")
    if h.mobility:
        bits.append(f"mobility: {h.mobility}")
    if not h.has_car:
        bits.append("no car")
    if h.kids_under_5:
        bits.append(f"{h.kids_under_5} kid(s) under 5")
    if h.language != "en":
        bits.append(f"language: {h.language}")
    return ", ".join(bits)


class TriageState(TypedDict, total=False):
    household: Household
    result: CallResult
    attempt: int
    reply: str
    decision: Decision | None
    problem: str


class ManagerAgent:
    """Implements the engine's triage interface (async decide) plus plan, re-plan and status."""

    def __init__(
        self,
        llm: LLMClient,
        db: Any = None,
        note: Callable[[str], None] | None = None,
        cache: bool = True,
        max_parallel: int = 8,
    ) -> None:
        self.llm = llm
        self.db = db  # where to record token usage per household (optional)
        self.note = note or (lambda text: None)
        self.cache = cache
        self.rules = RuleTriage()
        self._slots = asyncio.Semaphore(max_parallel)
        self._model_ok: bool | None = None  # last known state, so the feed only hears about changes
        self.stats = {"model": 0, "rules": 0, "skipped": 0, "retries": 0}
        self._triage_graph = self._build_triage_graph()

    source = "nemotron"

    # --- triage ------------------------------------------------------------------------------

    async def decide(self, h: Household, result: CallResult) -> Decision:
        # Clear cases skip the model: nobody reached, or the voice agent (Nemotron) heard a safe home.
        if not result.reached or (result.urgency in (None, Urgency.P4) and not result.needs):
            self.stats["skipped"] += 1
            return self.rules.decide_now(h, result)
        async with self._slots:
            state = await self._triage_graph.ainvoke({"household": h, "result": result, "attempt": 0})
        decision = state.get("decision")
        if decision is None:
            self.stats["rules"] += 1
            self._set_model_ok(False, state.get("problem", ""))
            return self.rules.decide_now(h, result)
        self.stats["model"] += 1
        self._set_model_ok(True)
        return decision

    def _build_triage_graph(self):
        graph = StateGraph(TriageState)
        graph.add_node("ask", self._ask_triage)
        graph.add_node("check", self._check_triage)
        graph.add_edge(START, "ask")
        graph.add_edge("ask", "check")
        graph.add_conditional_edges("check", self._after_check, {"done": END, "retry": "ask"})
        return graph.compile()

    async def _ask_triage(self, state: TriageState) -> TriageState:
        h, result = state["household"], state["result"]
        needs = "; ".join(f"{n.category.value} ({n.urgency.value}): {n.details}" for n in result.needs) or "none"
        user = (
            f"Household: {household_profile(h)}. People home: {result.people}.\n"
            f"Call summary: {result.summary}\nUrgency the call found: {(result.urgency or Urgency.P4).value}\n"
            f"Needs recorded: {needs}"
        )
        try:
            reply = await self.llm.chat(
                "manager", [{"role": "system", "content": TRIAGE_SYSTEM}, {"role": "user", "content": user}],
                thinking=False, max_tokens=300, temperature=0.2, cache=self.cache, timeout=20,
            )
        except LLMError as exc:
            return {"attempt": state["attempt"] + 1, "reply": "", "problem": f"model unavailable: {exc}"[:200]}
        self._record(reply, h.id)
        return {"attempt": state["attempt"] + 1, "reply": reply.text, "problem": ""}

    async def _check_triage(self, state: TriageState) -> TriageState:
        if state.get("problem", "").startswith("model unavailable"):
            return {"decision": None}
        decision, problem = self.parse_decision(state["household"], state["result"], state.get("reply", ""))
        return {"decision": decision, "problem": problem}

    def _after_check(self, state: TriageState) -> str:
        if state.get("decision") is not None or state.get("problem", "").startswith("model unavailable"):
            return "done"
        if state["attempt"] < 2:
            self.stats["retries"] += 1
            return "retry"
        return "done"

    def parse_decision(self, h: Household, result: CallResult, reply: str) -> tuple[Decision | None, str]:
        """Validate a model reply. Returns (decision, "") or (None, what was wrong)."""
        data = _first_json(reply)
        if data is None:
            return None, "reply was not JSON"
        try:
            urgency = Urgency(str(data.get("urgency", "")).upper())
        except ValueError:
            return None, f"bad urgency {data.get('urgency')!r}"
        found = result.urgency or Urgency.P4
        if RANK[urgency] > RANK[found]:
            urgency = found  # never less urgent than the call found
        help: list[HelpRequest] = []
        for item in data.get("help") or []:
            if not isinstance(item, dict):
                return None, "help item is not an object"
            try:
                kind = ActionType(str(item.get("kind", "")))
            except ValueError:
                return None, f"unknown help kind {item.get('kind')!r}"
            if kind not in HELP_KINDS:
                return None, f"help kind {kind.value} is not allowed"
            if any(r.kind is kind for r in help):
                continue
            reason = _clip(item.get("reason")) or (result.needs[0].details if result.needs else "needs a check")
            help.append(HelpRequest(kind, reason, needs_approval=kind in NEEDS_APPROVAL))
        if urgency is Urgency.P1 and not any(r.kind is ActionType.TRANSFER_TO_HUMAN for r in help):
            detail = result.needs[0].details if result.needs else "life in danger"
            help.insert(0, HelpRequest(ActionType.TRANSFER_TO_HUMAN, detail))
        if urgency in (Urgency.P2, Urgency.P3) and not help:
            return None, "a household that needs help got no help"
        return Decision(urgency, help, reason=_clip(data.get("reason"), 24), source="nemotron"), ""

    # --- plan, status, re-plan ------------------------------------------------------------------

    async def plan_summary(self, facts: dict[str, Any]) -> str | None:
        """The plan in one line, e.g. "312 high-risk homes first, in 3 waves." None means use the rule line."""
        prompt = (
            "You run a phone check-in after a tornado warning. State the call plan in one line of at most "
            "20 words, plain words, no preamble, like: \"312 high-risk homes first, in 3 waves.\" Facts:\n"
            + json.dumps(facts)
        )
        text = await self._short_text(prompt, thinking=True, max_tokens=1500, timeout=45)
        return text if text and len(text.split()) <= 30 else None

    async def status_text(self, counts: dict[str, Any], facts: dict[str, Any]) -> str | None:
        prompt = (
            "Write a status update for the human coordinator of a tornado phone check-in: at most 2 short "
            "sentences, plain words, numbers from the facts only, mention anything that needs their attention."
            f"\nCounts: {json.dumps(counts)}\nOther facts: {json.dumps(facts)}"
        )
        text = await self._short_text(prompt, thinking=False, max_tokens=200, timeout=20)
        return text if text and len(text.split()) <= 60 else None

    async def replan(self, trigger: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Pick one change from the menu for this trigger. Returns a validated change, or None."""
        prompt = (
            f"You run a tornado phone check-in. Something changed: {trigger}. Current state:\n"
            f"{json.dumps(context)}\nPick exactly one change from this menu:\n"
            + "\n".join(f"- {k}: {v}" for k, v in REPLAN_CHANGES.items())
            + '\nReply with JSON only: {"change": "<name>", ...its fields..., "why": "one short sentence"}'
        )
        for _ in range(2):
            try:
                reply = await self.llm.chat("manager", [{"role": "user", "content": prompt}], thinking=True,
                                            max_tokens=2000, temperature=0.2, cache=self.cache, timeout=45)
            except LLMError as exc:
                self._set_model_ok(False, str(exc))
                return None
            self._record(reply, None)
            change = self.validate_change(_first_json(reply.text))
            if change is not None:
                self._set_model_ok(True)
                return change
        return None

    @staticmethod
    def validate_change(data: dict[str, Any] | None) -> dict[str, Any] | None:
        if not data or data.get("change") not in REPLAN_CHANGES:
            return None
        kind, why = data["change"], _clip(data.get("why"), 24)
        try:
            if kind == "none":
                return {"change": "none", "why": why}
            if kind == "raise_priority_near":
                radius = min(max(float(data.get("radius_km", 1.0)), 0.3), 3.0)
                return {"change": kind, "lat": float(data["lat"]), "lng": float(data["lng"]),
                        "radius_km": radius, "why": why}
            if kind == "switch_help":
                pair = (str(data.get("from")), str(data.get("to")))
                return {"change": kind, "from": pair[0], "to": pair[1], "why": why} if pair in ALLOWED_SWITCHES else None
            if kind == "change_lines":
                return {"change": kind, "lines": min(max(int(data["lines"]), 10), 300), "why": why}
            if kind == "flag_coordinator":
                note = _clip(data.get("note"), 30)
                return {"change": kind, "note": note, "why": why} if note else None
        except (KeyError, TypeError, ValueError):
            return None
        return None

    # --- helpers --------------------------------------------------------------------------------

    async def _short_text(self, prompt: str, thinking: bool, max_tokens: int, timeout: float) -> str | None:
        try:
            reply = await self.llm.chat("manager", [{"role": "user", "content": prompt}], thinking=thinking,
                                        max_tokens=max_tokens, temperature=0.3, cache=self.cache, timeout=timeout)
        except LLMError as exc:
            self._set_model_ok(False, str(exc))
            return None
        self._record(reply, None)
        text = " ".join(reply.text.split()).strip('"')
        return text or None

    def _record(self, reply: LLMResult, household_id: int | None) -> None:
        if self.db is not None:
            try:
                record_usage(self.db, reply, household_id)
            except Exception:  # usage logging must never break a decision
                log.exception("could not record token usage")

    def _set_model_ok(self, ok: bool, problem: str = "") -> None:
        if ok == self._model_ok:
            return
        first = self._model_ok is None
        self._model_ok = ok
        if not ok:
            log.warning("manager model unavailable: %s", problem)
            self.note("Nemotron didn't answer, so decisions are running on the backup rules for now.")
        elif not first:
            self.note("Nemotron is back; it is making the decisions again.")
