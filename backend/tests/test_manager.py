"""M5 manager agent, against a fake Nemotron (no network, no keys)."""
import json

import pytest
from sqlmodel import select

from app.agent.manager import ManagerAgent
from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.db import Database
from app.engine.channels import CallResult, NeedReport
from app.engine.runs import run_sim
from app.geo import WarningArea
from app.llm import LLMError, LLMResult
from app.models import ActionType, CallOutcome, Channel, Event, Household, NeedCategory, Urgency

AREA = WarningArea.load(REPO_ROOT / "data" / "warning_area.geojson")


class FakeNemotron:
    """Answers by prompt type. `triage` may be a list of replies (one per call) or an exception."""

    providers = ["nvidia"]

    def __init__(self, triage=None, replan=None, plan="90 high-risk homes first, in 3 waves.",
                 status="430 called, 30 urgent; all crews are busy."):
        self.triage, self.replan, self.plan, self.status = triage, replan, plan, status
        self.calls = []

    async def chat(self, job, messages, **kwargs):
        text = messages[-1]["content"]
        system = messages[0]["content"] if messages[0]["role"] == "system" else ""
        kind = ("triage" if "manager agent of RollCall" in system else "plan" if "State the call plan" in text
                else "replan" if "Something changed" in text else "status")
        self.calls.append(kind)
        reply = getattr(self, kind)
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(text)
        return LLMResult(text=reply if isinstance(reply, str) else json.dumps(reply), job=job, provider="fake", model="fake")


def home(**kw):
    values = dict(id=1, name="Maria Lopez", phone="+15550000001", address="1412 Oak St, Richardson, TX",
                  lat=32.95, lng=-96.72, age=81, lives_alone=True, medical_device="oxygen concentrator")
    values.update(kw)
    return Household(**values)


def call(urgency=Urgency.P2, needs=(("power", "oxygen on battery, 2 hours left"),), outcome=CallOutcome.COMPLETED):
    return CallResult(household_id=1, attempt=1, channel=Channel.SCRIPTED, outcome=outcome, urgency=urgency,
                      needs=[NeedReport(NeedCategory(c), d, urgency) for c, d in needs], summary="Spoke with Maria.")


GOOD = {"urgency": "P2", "help": [{"kind": "dispatch_crew", "reason": "oxygen on battery"},
                                   {"kind": "deliver_generator", "reason": "device needs power"}],
        "reason": "Oxygen on battery, lives alone."}


async def test_model_decision_is_used_and_approval_kinds_wait():
    notes = []
    agent = ManagerAgent(FakeNemotron(triage=GOOD), note=notes.append)
    decision = await agent.decide(home(), call())
    assert decision.source == "nemotron" and decision.urgency is Urgency.P2
    assert [(r.kind, r.needs_approval) for r in decision.help] == [
        (ActionType.DISPATCH_CREW, False), (ActionType.DELIVER_GENERATOR, True)]
    assert notes == []


async def test_never_less_urgent_than_the_call_and_p1_always_hands_off():
    agent = ManagerAgent(FakeNemotron())
    lowered, _ = agent.parse_decision(home(), call(Urgency.P2), json.dumps({**GOOD, "urgency": "P4"}))
    assert lowered.urgency is Urgency.P2
    p1, _ = agent.parse_decision(home(), call(Urgency.P1), json.dumps({"urgency": "P1", "help": []}))
    assert p1.help[0].kind is ActionType.TRANSFER_TO_HUMAN


@pytest.mark.parametrize("reply", ["not json at all", json.dumps({"urgency": "P9"}),
                                   json.dumps({"urgency": "P2", "help": [{"kind": "launch_drone"}]}),
                                   json.dumps({"urgency": "P2", "help": []})])
def test_bad_replies_are_rejected(reply):
    decision, problem = ManagerAgent(FakeNemotron()).parse_decision(home(), call(), reply)
    assert decision is None and problem


async def test_malformed_twice_falls_back_to_rules_after_one_retry():
    fake, notes = FakeNemotron(triage=["oops", "still not json"]), []
    agent = ManagerAgent(fake, note=notes.append)
    decision = await agent.decide(home(), call())
    assert decision.source == "rules" and decision.urgency is Urgency.P2
    assert fake.calls == ["triage", "triage"] and agent.stats["retries"] == 1
    assert len(notes) == 1 and "backup rules" in notes[0]


async def test_outage_falls_back_and_the_feed_hears_once_then_recovery():
    fake, notes = FakeNemotron(triage=LLMError("nvidia: timeout")), []
    agent = ManagerAgent(fake, note=notes.append)
    for _ in range(3):
        assert (await agent.decide(home(), call())).source == "rules"
    fake.triage = GOOD
    assert (await agent.decide(home(), call())).source == "nemotron"
    assert len(notes) == 2 and "back" in notes[1]


async def test_safe_homes_and_no_answers_skip_the_model():
    fake = FakeNemotron(triage=GOOD)
    agent = ManagerAgent(fake)
    await agent.decide(home(), call(Urgency.P4, needs=()))
    await agent.decide(home(), call(None, needs=(), outcome=CallOutcome.NO_ANSWER))
    assert fake.calls == [] and agent.stats["skipped"] == 2


def test_replan_changes_are_validated():
    v = ManagerAgent.validate_change
    assert v({"change": "raise_priority_near", "lat": 32.9, "lng": -96.7, "radius_km": 99})["radius_km"] == 3.0
    assert v({"change": "switch_help", "from": "reserve_shelter_bed", "to": "hotel_voucher"})
    assert v({"change": "switch_help", "from": "dispatch_crew", "to": "hotel_voucher"}) is None
    assert v({"change": "change_lines", "lines": 5000})["lines"] == 300
    assert v({"change": "teleport"}) is None and v(None) is None


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("city") / "base.db"
    write_city(build_city(AREA, seed=11, n=400), path)
    return path


async def test_a_run_with_nemotron_plans_triages_replans_and_reports(base_db, tmp_path):
    def replan(prompt):
        if "P1" in prompt:
            return {"change": "raise_priority_near", "lat": 32.95, "lng": -96.72, "radius_km": 1.5,
                    "why": "Neighbors of an emergency are likely hurt too."}
        return {"change": "none", "why": "Nothing to change."}

    fake = FakeNemotron(triage=lambda prompt: GOOD if "P2" in prompt else
                        {"urgency": "P3", "help": [{"kind": "schedule_adjuster", "reason": "roof damage"},
                                                   {"kind": "open_claim", "reason": "roof damage"}]},
                        replan=replan)
    summary = await run_sim(base_db, tmp_path / "run.db", AREA, lines=30, llm=fake)
    assert summary["engine"] == "nemotron" and summary["called"] == summary["affected"]
    assert summary["decisions"]["model"] > 0 and summary["decisions"]["skipped"] > 0

    db = Database(tmp_path / "run.db")
    with db.session() as session:
        events = session.exec(select(Event)).all()
    db.dispose()
    texts = [e.payload.get("text", "") for e in events]
    assert any(t.startswith("Plan from Nemotron: 90 high-risk") for t in texts)
    assert any(e.type == "status.update" and "all crews are busy" in e.payload["text"] for e in events)
    if any(e.type == "household.status" and e.payload["status"] == "emergency" for e in events):
        assert any(t.startswith("Re-plan: calling") for t in texts) and summary["replans"] >= 1
