"""The response engine (M3): from an alert to every household called, triaged and helped.

Plain code owns the call queue, the phone lines, the status rules, matching and every timer.
Decisions go through a triage object (fixed rules now; Nemotron in M5, with the rules as its
fallback). Calls go through a channel (scripted now; phone in M6; simulated residents in M7).
The same engine runs on a sped-up real clock for demos and on a simulated clock for tests.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import statistics
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from app.clock import Clock, Timer
from app.db import Database, iso_utc
from app.engine.booking import COSTS, RESOURCE_FOR, Booker, Booking
from app.engine.channels import CallChannel, CallResult
from app.engine.triage import Decision, HelpRequest, RuleTriage
from app.events import EventBus, EventType
from app.geo import WarningArea
from app.models import (
    HELP_ACTIONS,
    NOT_REACHED,
    REACHED,
    URGENCY_STATUS,
    Action,
    ActionStatus,
    ActionType,
    Call,
    CallOutcome,
    CallPolicy,
    Channel,
    HiddenTruth,
    Household,
    Need,
    Resource,
    ResourceType,
    Status,
    Urgency,
)
from app.risk import call_order, wave_for

log = logging.getLogger(__name__)

STATUS_EVERY_S = 600  # a status update every 10 minutes on the run's clock

# Which status changes are legal. Anything else is a bug, except a downgrade of what we already
# know (urgent back to safe, say), which is ignored: new information can raise a household, never lower it.
ALLOWED: dict[Status, frozenset[Status]] = {
    Status.NOT_CALLED: frozenset({Status.CALLING}),
    Status.CALLING: frozenset({Status.SAFE, Status.NEEDS_HELP, Status.URGENT, Status.EMERGENCY, Status.NO_ANSWER}),
    Status.NO_ANSWER: frozenset({Status.CALLING, Status.ESCALATED, Status.SAFE, Status.NEEDS_HELP,
                                 Status.URGENT, Status.EMERGENCY}),
    Status.ESCALATED: frozenset({Status.CALLING, Status.SAFE, Status.NEEDS_HELP, Status.URGENT, Status.EMERGENCY}),
    Status.SAFE: frozenset({Status.NEEDS_HELP, Status.URGENT, Status.EMERGENCY}),
    Status.NEEDS_HELP: frozenset({Status.URGENT, Status.EMERGENCY}),
    Status.URGENT: frozenset({Status.EMERGENCY}),
    Status.EMERGENCY: frozenset(),
}
SEVERITY = {Status.SAFE: 1, Status.NEEDS_HELP: 2, Status.URGENT: 3, Status.EMERGENCY: 4}
LABEL = {
    ActionType.DELIVER_GENERATOR: "Generator",
    ActionType.HOTEL_VOUCHER: "Hotel voucher",
    ActionType.DISPATCH_CREW: "Crew visit",
    ActionType.IN_PERSON_CHECK: "In-person check",
}


class CallQueue:
    """Households waiting for a line, best first. A household can be re-queued with a new priority
    (M5 re-plans, M8 follow-ups); the old entry is dropped."""

    def __init__(self) -> None:
        self._heap: list[list[Any]] = []
        self._entries: dict[int, list[Any]] = {}
        self._seq = itertools.count()

    def push(self, household_id: int, priority: tuple) -> None:
        if household_id in self._entries:
            self._entries[household_id][-1] = False
        entry = [priority, next(self._seq), household_id, True]
        self._entries[household_id] = entry
        heapq.heappush(self._heap, entry)

    def pop(self) -> int | None:
        while self._heap:
            _, _, household_id, valid = heapq.heappop(self._heap)
            if valid:
                del self._entries[household_id]
                return household_id
        return None

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, household_id: int) -> bool:
        return household_id in self._entries


class Engine:
    def __init__(
        self,
        *,
        db: Database,
        bus: EventBus,
        clock: Clock,
        area: WarningArea,
        channel: CallChannel,
        triage: Any = None,
        policy: CallPolicy | str = CallPolicy.RISK_RANKED,
        lines: int = 100,
        live_channel: CallChannel | None = None,
        on_finish: Callable[[Engine], None] | None = None,
        approval_threshold: float = 500.0,
        text_sender: Callable[[Household, str], None] | None = None,
    ) -> None:
        # Sends follow-up texts. Default: none sent (the sms.sent event is the log). Real texts to
        # consenting live phones arrive with the phone work in M6.
        self.text_sender = text_sender or (lambda household, body: None)
        self.db, self.bus, self.clock, self.area = db, bus, clock, area
        self.channel = channel
        self.live_channel = live_channel  # real phones (M6); without it, live demo homes are skipped
        self.triage = triage or RuleTriage()
        self.policy = CallPolicy(policy)
        self.lines = lines
        self.on_finish = on_finish
        self.queue = CallQueue()
        self.households: dict[int, Household] = {}
        self.attempts: Counter[int] = Counter()
        self.active = 0  # calls in progress
        self.pending = 0  # finished calls still being triaged
        self.calls_placed = 0
        self.help_at: dict[int, datetime] = {}  # when help first went out to each household
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.booker = Booker([])
        self._status_timer: Timer | None = None
        self._busy_noted: set[ResourceType] = set()
        self.approval_threshold = approval_threshold
        self.next_step: dict[int, dict[str, Any]] = {}  # the next follow-up step per household (detail panel)
        self.followups: dict[int, Timer] = {}  # households waiting for their next follow-up step
        self._followup_seq = itertools.count()
        self._first_pass_noted = False
        self.rank: dict[int, int] = {}  # each household's place in the original call order
        self.help_swaps: dict[ActionType, ActionType] = {}  # set by re-plans, applied at booking
        self._last_replan: dict[str, datetime] = {}
        self.replans = 0

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    # --- run lifecycle --------------------------------------------------------------------

    async def start(self) -> None:
        self.started_at = self.clock.now()
        with self.db.session() as session:
            households = session.exec(select(Household)).all()
            resources = session.exec(select(Resource)).all()
        self.booker = Booker(list(resources))
        affected = [h for h in households if self.area.contains(h.lat, h.lng)]
        self.households = {h.id: h for h in affected}
        waves = Counter()
        with self.db.session() as session:
            for h in affected:
                h.wave = wave_for(h.risk_score)
                waves[h.wave] += 1
                session.add(h)
            session.commit()

        self._emit(EventType.ALERT_RECEIVED, {
            "title": self.area.title, "source": self.area.feature.get("properties", {}).get("source", "saved"),
            "polygon": self.area.feature["geometry"], "affected_ids": sorted(self.households),
            "text": f"{self.area.title} received: {len(affected):,} homes inside the warning area.",
        })
        ordered = call_order(affected, self.policy)
        to_call = [h for h in ordered if not h.live or self.live_channel is not None]
        if self.policy is CallPolicy.RISK_RANKED:
            summary = f"{waves[1]} high-risk homes first, in 3 waves."
        else:
            summary = f"All {len(to_call):,} homes in list order."
        plan = {"summary": summary, "affected": len(affected), "waves": {str(w): waves[w] for w in (1, 2, 3)},
                "policy": self.policy, "lines": self.lines}
        if hasattr(self.triage, "plan_summary"):
            # Calls start now; Nemotron's one-line plan follows as soon as it's written.
            self._emit(EventType.PLAN_CREATED, plan)
            facts = self._plan_facts(affected, waves)
            self.clock.call_later(0, lambda: self._model_plan(plan, facts))
        else:
            self._emit(EventType.PLAN_CREATED, {**plan, "text": f"Plan: {summary} Calling up to {self.lines} homes at a time."})
        skipped = len(ordered) - len(to_call)
        if skipped:
            self._emit(EventType.AGENT_NOTE, {"text": f"{skipped} live demo home(s) wait for a real phone line (M6)."})
        for rank, h in enumerate(to_call):
            self.rank[h.id] = rank
            self.queue.push(h.id, (rank,))
        self._status_timer = self.clock.call_later(STATUS_EVERY_S, self._status_tick)
        self._fill()

    def _plan_facts(self, affected: list[Household], waves: Counter) -> dict[str, Any]:
        return {
            "homes_in_warning_area": len(affected),
            "waves_by_risk": {"high": waves[1], "medium": waves[2], "low": waves[3]},
            "call_order": self.policy.value, "phone_lines": self.lines,
            "with_powered_medical_device": sum(1 for h in affected if h.medical_device),
            "age_75_plus_living_alone": sum(1 for h in affected if h.age >= 75 and h.lives_alone),
            "crews": len(self.booker.pool(ResourceType.CREW)),
            "shelter_beds": sum(r.capacity for r in self.booker.pool(ResourceType.SHELTER)),
        }

    async def _model_plan(self, plan: dict[str, Any], facts: dict[str, Any]) -> None:
        line = await self.triage.plan_summary(facts)
        if line:
            self._emit(EventType.PLAN_CREATED, {**plan, "summary": line, "source": "nemotron", "text": f"Plan from Nemotron: {line}"})
        else:
            self._emit(EventType.PLAN_CREATED, {**plan, "text": f"Plan: {plan['summary']} Calling up to {self.lines} homes at a time."})

    # --- re-planning (M5) ---------------------------------------------------------------------

    def _trigger(self, kind: str, detail: dict[str, Any], every_s: float = 600) -> None:
        """Something changed. If the brain can re-plan and hasn't for this reason lately, ask it."""
        if not hasattr(self.triage, "replan") or self.finished:
            return
        now = self.clock.now()
        last = self._last_replan.get(kind)
        if last is not None and (now - last).total_seconds() < every_s:
            return
        self._last_replan[kind] = now
        self.clock.call_later(0, lambda: self._replan(kind, detail))

    def replan_context(self, detail: dict[str, Any]) -> dict[str, Any]:
        now = self.clock.now()
        crews = self.booker.pool(ResourceType.CREW)
        shelters = self.booker.pool(ResourceType.SHELTER)
        nxt = self.booker.next_free(ResourceType.CREW, now)
        return {
            **detail, "counts": self.counts(), "waiting_to_be_called": len(self.queue), "phone_lines": self.lines,
            "crews_busy": sum(1 for r in crews if (r.free_at or now) > now), "crews_total": len(crews),
            "next_crew_free_in_min": round((nxt - now).total_seconds() / 60) if nxt else None,
            "shelter_beds_used": sum(r.used for r in shelters), "shelter_beds_total": sum(r.capacity for r in shelters),
            "help_switches_in_effect": {k.value: v.value for k, v in self.help_swaps.items()},
        }

    async def _replan(self, kind: str, detail: dict[str, Any]) -> None:
        change = await self.triage.replan(kind, self.replan_context(detail))
        if change is None or self.finished:
            return
        self.apply_change(change)

    def apply_change(self, change: dict[str, Any]) -> None:
        """Apply one validated change from the re-plan menu and say what changed."""
        kind, why = change["change"], change.get("why", "")
        text = None
        if kind == "raise_priority_near":
            moved = 0
            for hid, h in self.households.items():
                if hid in self.queue and _km(h.lat, h.lng, change["lat"], change["lng"]) <= change["radius_km"]:
                    self.queue.push(hid, (-1, self.rank.get(hid, 0)))
                    moved += 1
            text = f"Re-plan: calling {moved} homes within {change['radius_km']:g} km sooner. {why}"
        elif kind == "switch_help":
            self.help_swaps[ActionType(change["from"])] = ActionType(change["to"])
            text = f"Re-plan: using {change['to'].replace('_', ' ')} instead of {change['from'].replace('_', ' ')}. {why}"
        elif kind == "change_lines":
            old, self.lines = self.lines, change["lines"]
            text = f"Re-plan: {self.lines} calls at once instead of {old}. {why}"
            self._fill()
        elif kind == "flag_coordinator":
            text = f"For the coordinator: {change['note']}. {why}"
        if text:
            self.replans += 1
            self._emit(EventType.AGENT_NOTE, {"text": text.strip(), "reason": "replan", "change": change})

    def _fill(self) -> None:
        while self.active < self.lines and len(self.queue):
            household_id = self.queue.pop()
            self._start_call(self.households[household_id])
        self._check_done()

    def _check_done(self) -> None:
        if self.started_at is None or self.finished:
            return
        if self.active or self.pending or len(self.queue):
            return
        if not self._first_pass_noted:
            self._first_pass_noted = True
            c = self.counts()
            minutes = (self.clock.now() - self.started_at).total_seconds() / 60
            waiting = f" Following up with {len(self.followups):,} homes nobody reached." if self.followups else ""
            self._emit(EventType.AGENT_NOTE, {
                "counts": c,
                "text": (f"First pass done in {minutes:.0f} minutes: {c['reached']:,} reached, {c['urgent']} urgent, "
                         f"{c['help_sent']} with help on the way.{waiting}"),
            })
        if not self.followups:
            self._finish()

    def _finish(self) -> None:
        self.finished_at = self.clock.now()
        if self._status_timer:
            self._status_timer.cancel()
        counts = self.counts()
        statuses = Counter(h.status for h in self.households.values())
        minutes = (self.finished_at - self.started_at).total_seconds() / 60
        self._emit(EventType.AGENT_NOTE, {
            "counts": counts,
            "text": (f"Every home is accounted for after {minutes:.0f} minutes: {counts['reached']:,} reached, "
                     f"{statuses[Status.ESCALATED]} getting an in-person check, {counts['not_reached']} not reached. "
                     f"{counts['urgent']} urgent, {counts['help_sent']} with help on the way."),
        })
        if self.on_finish:
            self.on_finish(self)

    # --- follow-up ladder (M8) ------------------------------------------------------------------

    def _follow_up(self, h: Household) -> None:
        """Nobody was reached: take the next step on the ladder (docs/API.md)."""
        attempt = self.attempts[h.id]
        now = self.clock.now()
        if attempt == 1:
            due = now + timedelta(minutes=10)
            self._wait(h, due, lambda: self._requeue(h))
            self._emit(EventType.FOLLOWUP_SCHEDULED, {"step": 1, "channel": "call", "at": iso_utc(due)}, h.id)
            self.next_step[h.id] = {"step": 1, "channel": "call", "at": iso_utc(due)}
        elif attempt == 2:
            body = "RollCall for State Farm: we couldn't reach you about the tornado warning. Reply 1 safe, 2 need help, 3 emergency."
            self.text_sender(h, body)
            self._emit(EventType.SMS_SENT, {"body": body, "step": 2}, h.id)
            self._emit(EventType.FOLLOWUP_SCHEDULED, {"step": 2, "channel": "text", "at": iso_utc(now)}, h.id)
            reply = getattr(self.channel, "text_reply", None)
            answer = reply(h) if reply and not h.live else None
            if answer is not None and answer[0] < 20 * 60:
                delay, text = answer
                self.clock.call_later(delay, lambda: self.handle_text_reply(h, text))
            due = now + timedelta(minutes=20)
            self._wait(h, due, lambda: self._requeue(h))
            self._emit(EventType.FOLLOWUP_SCHEDULED, {"step": 3, "channel": "call", "at": iso_utc(due)}, h.id)
            self.next_step[h.id] = {"step": 3, "channel": "call", "at": iso_utc(due)}
        else:
            self._set_status(h, Status.ESCALATED)
            self._emit(EventType.FOLLOWUP_SCHEDULED, {"step": 4, "channel": "in_person", "at": iso_utc(now)}, h.id)
            self.next_step.pop(h.id, None)
            self._act(h, CallResult(h.id, attempt, self.channel.name, CallOutcome.NO_ANSWER),
                      HelpRequest(ActionType.IN_PERSON_CHECK, "no answer after 3 calls and a text"))

    def _wait(self, h: Household, due: datetime, step: Callable[[], None]) -> None:
        def fire() -> None:
            self.followups.pop(h.id, None)
            step()
            self._check_done()
        self.followups[h.id] = self.clock.call_at(due, fire)

    def _requeue(self, h: Household) -> None:
        if h.status is not Status.NO_ANSWER or self.finished:
            return  # resolved meanwhile (a text reply)
        self.queue.push(h.id, (-2, next(self._followup_seq)))  # due follow-ups go before first calls
        self._fill()

    def handle_text_reply(self, h: Household, body: str) -> None:
        """A text came in from this household: "1" safe, "2" need help, "3" emergency, or free text."""
        timer = self.followups.pop(h.id, None)
        self.next_step.pop(h.id, None)
        if timer:
            timer.cancel()
        clean = body.strip()
        parsed = {"1": "safe", "2": "needs_help", "3": "emergency"}.get(clean[:1] if len(clean) <= 3 else "", "free_text")
        street = h.address.split(",")[0]
        label = {"safe": "safe", "needs_help": "needs help", "emergency": "EMERGENCY"}.get(parsed, f'"{clean[:80]}"')
        self._emit(EventType.SMS_RECEIVED, {"body": clean[:300], "parsed": parsed,
                                            "text": f"Text reply from {street}: {label}."}, h.id)
        if parsed == "safe":
            decision = Decision(Urgency.P4, reason="replied 1 (safe)")
        elif parsed == "emergency":
            decision = Decision(Urgency.P1, [HelpRequest(ActionType.TRANSFER_TO_HUMAN, "replied 3 (emergency) to the text"),
                                             HelpRequest(ActionType.DISPATCH_CREW, "replied 3 (emergency) to the text")])
        else:
            reason = "replied 2 (needs help) to the text" if parsed == "needs_help" else f'texted: "{_clip_words(clean, 10)}"'
            decision = Decision(Urgency.P3, [HelpRequest(ActionType.DISPATCH_CREW, reason)])
        if h.status is Status.CALLING:
            self._set_status(h, URGENCY_STATUS[decision.urgency])  # the call in progress can't lower it
        result = CallResult(h.id, self.attempts[h.id], Channel.SMS, CallOutcome.COMPLETED, urgency=decision.urgency)
        self._apply(h, result, decision)
        self._check_done()

    # --- approvals (M8) ---------------------------------------------------------------------------

    def decide_approval(self, action_id: int, approved: bool, by: str = "coordinator") -> Action:
        """The coordinator approved or rejected a costly action. Approved help is booked now;
        a rejection gets a cheaper alternative."""
        with self.db.session() as session:
            action = session.get(Action, action_id)
        if action is None:
            raise KeyError(action_id)
        if action.status is not ActionStatus.PENDING_APPROVAL:
            raise ValueError("already decided")
        h = self.households.get(action.household_id)
        if h is None:
            with self.db.session() as session:
                h = session.get(Household, action.household_id)
        street = h.address.split(",")[0]
        label = LABEL.get(action.type, action.type.value)
        now = self.clock.now()
        booking = None
        if approved and action.type in RESOURCE_FOR:
            booking = self.booker.book(action.type, h, now)
            if booking:
                self._save_resource(booking.resource)
        with self.db.session() as session:
            row = session.get(Action, action_id)
            row.status = ActionStatus.SCHEDULED if approved else ActionStatus.REJECTED
            row.approved_by, row.updated_at = by, now
            if booking:
                row.resource_id, row.scheduled_for = booking.resource.id, booking.arrive_at
                row.details = {"start_at": booking.start_at.isoformat(), "arrive_at": booking.arrive_at.isoformat(),
                               "done_at": booking.done_at.isoformat() if booking.done_at else None,
                               "distance_km": round(booking.distance_km, 2), "beds": booking.beds}
            session.add(row)
            session.commit()
            action = row
        if approved:
            self.help_at.setdefault(h.id, now)
            where = f" {booking.resource.name} arriving {_soon(booking.eta_minutes(now))}." if booking else ""
            text = f"Approved by the {by}: {label.lower()} for {street}.{where}"
        else:
            text = f"Rejected by the {by}: {label.lower()} for {street}."
        self._emit(EventType.APPROVAL_DECIDED, {"action_id": action_id, "type": action.type, "approved": approved,
                                                "by": by, "text": text}, h.id)
        self._emit(EventType.ACTION_UPDATED, {"action_id": action_id, "status": action.status}, h.id)
        if booking and booking.done_at:
            self.clock.call_at(booking.done_at, lambda: self._job_done(action_id, h.id))
        if not approved:
            self._alternative(h, action)
        return action

    def _alternative(self, h: Household, action: Action) -> None:
        result = CallResult(h.id, self.attempts[h.id], self.channel.name, CallOutcome.COMPLETED, people=2)
        if action.type is ActionType.DELIVER_GENERATOR:
            self._act(h, result, HelpRequest(ActionType.DISPATCH_CREW, f"spare batteries instead of a generator: {action.reason}"))
        elif action.type is ActionType.HOTEL_VOUCHER:
            if any(r.capacity - r.used >= 2 for r in self.booker.pool(ResourceType.SHELTER)):
                self._act(h, result, HelpRequest(ActionType.RESERVE_SHELTER_BED, action.reason))
            else:
                self._emit(EventType.AGENT_NOTE, {"text": f"No shelter room and the voucher was rejected: {h.address.split(',')[0]} needs a decision.",
                                                  "reason": "coordinator"}, h.id)

    async def _status_tick(self) -> None:
        if self.finished:
            return
        self._status_timer = self.clock.call_later(STATUS_EVERY_S, self._status_tick)
        c = self.counts()
        text = (f"Status: {c['called']:,} of {c['affected']:,} called, {c['reached']:,} reached, "
                f"{c['urgent']} urgent, {c['help_sent']} with help on the way, {c['not_reached']:,} not reached yet.")
        if hasattr(self.triage, "status_text"):
            context = self.replan_context({})
            written = await self.triage.status_text(c, {k: v for k, v in context.items() if k != "counts"})
            if written:
                text = f"Status: {written}" if not written.lower().startswith("status") else written
        if not self.finished:
            self._emit(EventType.STATUS_UPDATE, {"counts": c, "text": text})

    # --- calls ----------------------------------------------------------------------------

    def _start_call(self, h: Household) -> None:
        self.next_step.pop(h.id, None)
        self.attempts[h.id] += 1
        attempt = self.attempts[h.id]
        self.active += 1
        self.calls_placed += 1
        self._set_status(h, Status.CALLING)
        channel = self.live_channel if (h.live and self.live_channel) else self.channel
        with self.db.session() as session:
            call = Call(household_id=h.id, channel=channel.name, attempt=attempt, started_at=self.clock.now())
            session.add(call)
            session.commit()
            call_id = call.id
        self._emit(EventType.CALL_STARTED, {"call_id": call_id, "channel": channel.name, "attempt": attempt}, h.id)
        channel.start(h, attempt, lambda result: self._on_result(h, call_id, result))

    async def _on_result(self, h: Household, call_id: int, result: CallResult) -> None:
        self.active -= 1
        self.pending += 1
        try:
            self._save_call(call_id, result)
            self._emit(EventType.CALL_ENDED,
                       {"call_id": call_id, "outcome": result.outcome, "summary": result.summary}, h.id)
            self._fill()  # the line is free: start the next call before deciding about this one
            if result.reached:
                self._save_needs(h, call_id, result)
            decision = await self.triage.decide(h, result)
            self._apply(h, result, decision)
        finally:
            self.pending -= 1
            self._check_done()

    def _apply(self, h: Household, result: CallResult, decision: Decision) -> None:
        if decision.urgency is None:
            if h.status in REACHED or h.status is Status.ESCALATED:
                return  # resolved meanwhile (say, a text reply during the call)
            self._set_status(h, Status.NO_ANSWER)
            self._follow_up(h)
            return
        self._set_status(h, URGENCY_STATUS[decision.urgency])
        for request in decision.help:
            if decision.urgency is Urgency.P3 and request.kind is ActionType.DISPATCH_CREW:
                request = HelpRequest(request.kind, request.reason, request.needs_approval, deferred=True)
            self._act(h, result, request)
        if decision.urgency is Urgency.P1:
            self._trigger("a new P1 emergency", {"emergency_lat": h.lat, "emergency_lng": h.lng,
                                                  "emergency": decision.reason}, every_s=300)

    # --- help -----------------------------------------------------------------------------

    def _act(self, h: Household, result: CallResult, request: HelpRequest) -> None:
        now = self.clock.now()
        street = h.address.split(",")[0]
        if request.kind in self.help_swaps:  # a re-plan switched this kind of help
            swapped = self.help_swaps[request.kind]
            request = HelpRequest(swapped, request.reason,
                                  needs_approval=swapped in (ActionType.DELIVER_GENERATOR, ActionType.HOTEL_VOUCHER))
        kind = request.kind
        cost = COSTS.get(kind, 0.0)
        if request.needs_approval or cost > self.approval_threshold:
            action = self._add_action(h, kind, ActionStatus.PENDING_APPROVAL, request.reason, cost, True, None)
            self._emit(EventType.APPROVAL_REQUESTED, {
                "action_id": action.id, "type": kind, "cost": cost, "reason": request.reason,
                "name": h.name, "address": h.address, "urgency_status": h.status,
                "text": f"{LABEL.get(kind, kind.value)} for {street} is waiting for approval "
                        f"(${cost:,.0f}): {request.reason}.",
            }, h.id)
            return
        booking: Booking | None = None
        if request.deferred:  # within a day: on the next-day crew list, not taking a crew now
            action = self._add_action(h, kind, ActionStatus.SCHEDULED, request.reason, cost, False, None)
            self.help_at.setdefault(h.id, now)
            self._emit(EventType.ACTION_CREATED, {
                "action_id": action.id, "type": kind, "status": action.status, "resource_id": None,
                "reason": request.reason, "deferred": True,
                "text": f"Added {street} to the next-day crew list: {request.reason}.",
            }, h.id)
            return
        if kind in RESOURCE_FOR:
            booking = self.booker.book(kind, h, now, people=max(result.people, 1))
            if booking is None:  # only shelters fill up
                self._emit(EventType.AGENT_NOTE, {"text": f"Every shelter is full. {street} needs another place to stay."}, h.id)
                self._trigger("every shelter is full", {})
                self._act(h, result, HelpRequest(ActionType.HOTEL_VOUCHER, request.reason, needs_approval=True))
                return
            self._save_resource(booking.resource)
        action = self._add_action(h, kind, ActionStatus.SCHEDULED, request.reason, cost, False, booking)
        if kind in HELP_ACTIONS:
            self.help_at.setdefault(h.id, now)
        payload: dict[str, Any] = {
            "action_id": action.id, "type": kind, "status": action.status,
            "resource_id": booking.resource.id if booking else None, "reason": request.reason,
        }
        if booking:
            payload["eta_minutes"] = booking.eta_minutes(now)
        text = _describe(kind, street, request.reason, booking, now)
        if text:
            payload["text"] = text
        self._emit(EventType.ACTION_CREATED, payload, h.id)
        if booking and booking.done_at:
            action_id = action.id
            self.clock.call_at(booking.done_at, lambda: self._job_done(action_id, h.id))
            rtype = booking.resource.type
            if rtype not in self._busy_noted and self.booker.all_busy(rtype, now):
                self._busy_noted.add(rtype)
                wait = round((self.booker.next_free(rtype, now) - now).total_seconds() / 60)
                self._emit(EventType.AGENT_NOTE, {
                    "text": f"Every {rtype.value} is out on a job now; the next one frees up in about {wait} min.",
                    "reason": "resources_busy",
                })
                self._trigger(f"every {rtype.value} is busy", {"next_free_in_min": wait})

    def _job_done(self, action_id: int, household_id: int) -> None:
        with self.db.session() as session:
            action = session.get(Action, action_id)
            if action is None or action.status is not ActionStatus.SCHEDULED:
                return
            action.status, action.updated_at = ActionStatus.DONE, self.clock.now()
            session.add(action)
            session.commit()
        self._emit(EventType.ACTION_UPDATED, {"action_id": action_id, "status": ActionStatus.DONE}, household_id)

    # --- state ------------------------------------------------------------------------------

    def _set_status(self, h: Household, new: Status) -> None:
        old = h.status
        if new == old:
            return
        if new not in ALLOWED[old]:
            if old in SEVERITY and new in SEVERITY and SEVERITY[new] < SEVERITY[old]:
                return  # never lower what we already know
            if (old in REACHED or old is Status.ESCALATED) and new in NOT_REACHED:
                return  # "not reached" never goes back up
            raise ValueError(f"household {h.id}: {old} -> {new} is not an allowed status change")
        h.status = new
        with self.db.session() as session:
            row = session.get(Household, h.id)
            row.status = new
            session.add(row)
            session.commit()
        self._emit(EventType.HOUSEHOLD_STATUS, {"status": new, "previous": old}, h.id)

    def counts(self) -> dict[str, int]:
        statuses = Counter(h.status for h in self.households.values())
        affected = len(self.households)
        return {
            "affected": affected,
            "called": affected - statuses[Status.NOT_CALLED],
            "reached": sum(statuses[s] for s in REACHED),
            "urgent": statuses[Status.URGENT] + statuses[Status.EMERGENCY],
            "help_sent": len(self.help_at),
            "not_reached": sum(statuses[s] for s in NOT_REACHED),
        }

    def summary(self, truths: dict[int, HiddenTruth] | None = None) -> dict[str, Any]:
        """Counts and timings for this run; with hidden truths, how fast urgent homes got help."""
        end = self.finished_at or self.clock.now()
        out: dict[str, Any] = {
            **self.counts(), "policy": self.policy.value, "lines": self.lines, "calls": self.calls_placed,
            "minutes": round((end - self.started_at).total_seconds() / 60, 1),
        }
        if truths:
            urgent = [i for i in self.households if i in truths
                      and truths[i].true_urgency in (Urgency.P1, Urgency.P2)]
            waits = sorted((self.help_at[i] - self.started_at).total_seconds() / 60
                           for i in urgent if i in self.help_at)
            out.update({
                "true_urgent": len(urgent),
                "true_urgent_helped": len(waits),
                "minutes_until_all_reached_urgent_helped": round(waits[-1], 1) if waits else None,
                "median_minutes_to_help_urgent": round(statistics.median(waits), 1) if waits else None,
            })
        return out

    # --- persistence --------------------------------------------------------------------------

    def _emit(self, type: EventType, payload: dict[str, Any], household_id: int | None = None) -> None:
        self.bus.emit(type, payload, household_id)

    def _save_call(self, call_id: int, result: CallResult) -> None:
        with self.db.session() as session:
            call = session.get(Call, call_id)
            call.ended_at = self.clock.now()
            call.outcome = result.outcome
            call.summary = result.summary
            call.transcript = list(result.transcript)
            call.external_id = result.external_id
            session.add(call)
            session.commit()

    def _save_needs(self, h: Household, call_id: int, result: CallResult) -> None:
        now = self.clock.now()
        saved = []
        with self.db.session() as session:
            for n in result.needs:
                need = Need(household_id=h.id, call_id=call_id, category=n.category, details=n.details,
                            urgency=n.urgency, created_at=now)
                session.add(need)
                saved.append(need)
            session.commit()
        for need in saved:  # no "text": only live calls put needs in the feed (M6)
            self._emit(EventType.NEED_RECORDED, {
                "category": need.category, "details": need.details, "urgency": need.urgency,
                "call_id": call_id, "need_id": need.id,
            }, h.id)

    def _add_action(self, h: Household, kind: ActionType, status: ActionStatus, reason: str, cost: float,
                    needs_approval: bool, booking: Booking | None) -> Action:
        now = self.clock.now()
        details: dict[str, Any] = {}
        if booking:
            details = {
                "start_at": booking.start_at.isoformat(), "arrive_at": booking.arrive_at.isoformat(),
                "done_at": booking.done_at.isoformat() if booking.done_at else None,
                "distance_km": round(booking.distance_km, 2), "beds": booking.beds,
            }
        with self.db.session() as session:
            action = Action(
                household_id=h.id, type=kind, status=status, resource_id=booking.resource.id if booking else None,
                cost=cost, needs_approval=needs_approval, reason=reason, details=details,
                scheduled_for=booking.arrive_at if booking else None, created_at=now, updated_at=now,
            )
            session.add(action)
            session.commit()
            return action

    def _save_resource(self, r: Resource) -> None:
        with self.db.session() as session:
            row = session.get(Resource, r.id)
            row.used, row.free_at = r.used, r.free_at
            session.add(row)
            session.commit()


def _clip_words(text: str, words: int) -> str:
    return " ".join(text.split()[:words])


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from app.geo import haversine_km

    return haversine_km(lat1, lng1, lat2, lng2)


def _soon(minutes: int) -> str:
    if minutes <= 1:
        return "now"
    if minutes < 90:
        return f"in about {minutes} min"
    return f"in about {round(minutes / 60)} h" if minutes < 36 * 60 else f"in about {round(minutes / 1440)} days"


def _describe(kind: ActionType, street: str, reason: str, booking: Booking | None, now: datetime) -> str | None:
    """One plain-words feed line per action, or None for bookkeeping actions."""
    eta = booking.eta_minutes(now) if booking else 0
    name = booking.resource.name if booking else ""
    if kind is ActionType.DISPATCH_CREW:
        return f"Sent {name} to {street}: {reason}. Arriving {_soon(eta)}."
    if kind is ActionType.IN_PERSON_CHECK:
        return f"Sent {name} for an in-person check at {street}. Arriving {_soon(eta)}."
    if kind is ActionType.RESERVE_SHELTER_BED:
        beds = booking.beds if booking else 1
        return f"Held {beds} bed{'s' if beds != 1 else ''} at {name} for {street}: {reason}."
    if kind is ActionType.SCHEDULE_ADJUSTER:
        return f"Booked {name} for {street}: {reason}. Visit {_soon(eta)}; claim opened."
    if kind is ActionType.TRANSFER_TO_HUMAN:
        return f"Emergency at {street}: {reason}. Told them to call 911 and handed off to a dispatcher."
    if kind is ActionType.DELIVER_GENERATOR:
        return f"{name} on its way to {street}: {reason}. Arriving {_soon(eta)}."
    return None
