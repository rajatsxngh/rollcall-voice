"""M8: the follow-up ladder, text replies and approvals, on simulated runs."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.db import Database
from app.engine.runs import run_sim
from app.geo import WarningArea
from app.main import create_app
from app.models import Action, ActionStatus, ActionType, Event, HiddenTruth, Household, Persona, Status

AREA = WarningArea.load(REPO_ROOT / "data" / "warning_area.geojson")


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("city") / "base.db"
    write_city(build_city(AREA, seed=11, n=500), path)
    return path


def read(path, model):
    db = Database(path)
    with db.session() as session:
        rows = session.exec(select(model)).all()
    db.dispose()
    return rows


async def test_ladder_leaves_nobody_unaccounted_for(base_db, tmp_path):
    summary = await run_sim(base_db, tmp_path / "run.db", AREA, lines=50)
    homes = {h.id: h for h in read(tmp_path / "run.db", Household) if AREA.contains(h.lat, h.lng)}
    truths = {t.household_id: t for t in read(tmp_path / "run.db", HiddenTruth)}
    assert summary["not_reached"] == 0
    assert all(h.status not in (Status.NOT_CALLED, Status.CALLING, Status.NO_ANSWER) for h in homes.values())

    never = [hid for hid in homes if truths[hid].persona_style is Persona.NEVER_ANSWERS]
    assert never
    for hid in never:  # never answer the phone: either their text reply resolved them, or a crew is going
        if truths[hid].replies_to_text:
            continue
        assert homes[hid].status is Status.ESCALATED

    events = read(tmp_path / "run.db", Event)
    checks = [a for a in read(tmp_path / "run.db", Action) if a.type is ActionType.IN_PERSON_CHECK]
    assert {a.household_id for a in checks} == {hid for hid, h in homes.items() if h.status is Status.ESCALATED}
    steps = {(e.household_id, e.payload["step"]) for e in events if e.type == "followup.scheduled"}
    escalated = next(hid for hid, h in homes.items() if h.status is Status.ESCALATED)
    assert {(escalated, s) for s in (1, 2, 3, 4)} <= steps


async def test_not_reached_never_goes_up(base_db, tmp_path):
    await run_sim(base_db, tmp_path / "run.db", AREA, lines=50)
    events = sorted(read(tmp_path / "run.db", Event), key=lambda e: e.id)
    reached = {"safe", "needs_help", "urgent", "emergency", "escalated"}
    resolved: set[int] = set()
    for e in events:
        if e.type != "household.status":
            continue
        if e.payload["status"] in reached:
            resolved.add(e.household_id)
        else:
            assert e.household_id not in resolved, e.payload


async def test_text_replies_resolve_households(base_db, tmp_path):
    await run_sim(base_db, tmp_path / "run.db", AREA, lines=50)
    replies = [e for e in read(tmp_path / "run.db", Event) if e.type == "sms.received"]
    assert replies and all(e.payload["text"].startswith("Text reply from") for e in replies)


@pytest.fixture
def client(settings):
    shutil.copy(REPO_ROOT / "data" / "warning_area.geojson", settings.data_dir / "warning_area.geojson")
    write_city(build_city(AREA, seed=3, n=300), settings.live_db_path)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def wait_for(client, done, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = done()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_approve_books_the_generator_and_reject_sends_an_alternative(client):
    client.post("/alerts/demo", params={"speed": 1000})
    approvals = wait_for(client, lambda: client.get("/approvals").json()["approvals"])
    first = approvals[0]
    assert first["type"] in ("deliver_generator", "hotel_voucher") and first["cost"] > 0

    approved = client.post(f"/approvals/{first['action_id']}/approve", json={"by": "coordinator"}).json()
    assert approved["status"] == "scheduled"
    assert client.post(f"/approvals/{first['action_id']}/approve").status_code == 409
    assert client.post("/approvals/999999/approve").status_code == 404

    second = wait_for(client, lambda: [a for a in client.get("/approvals").json()["approvals"]
                                       if a["action_id"] != first["action_id"]])[0]
    rejected = client.post(f"/approvals/{second['action_id']}/reject").json()
    assert rejected["status"] == "rejected"
    events = client.get("/events", params={"limit": 5000}).json()["events"]
    decided = [e for e in events if e["type"] == "approval.decided"]
    assert [e["payload"]["approved"] for e in decided] == [True, False]
    if second["type"] == "deliver_generator":
        assert any(e["type"] == "action.created" and e["household_id"] == second["household_id"]
                   and "spare batteries" in e["payload"]["reason"] for e in events)
    client.post("/reset")


def test_inbound_text_and_household_detail(client):
    client.post("/alerts/demo", params={"speed": 1000})
    wait_for(client, lambda: client.get("/state").json()["run"]["active"] is False)
    state = client.get("/state").json()
    hid = next(h["id"] for h in state["households"] if h["affected"])
    detail = client.get(f"/households/{hid}").json()
    assert detail["id"] == hid and "phone" not in detail and detail["calls"]
    assert detail["risk_factors"] == sorted(detail["risk_factors"], key=lambda f: -f["points"])
    assert client.get("/households/999999").status_code == 404

    phone = "+15550000001"  # a fake household number; no run active anymore, so it's just logged
    resp = client.post("/sms/inbound", content=f"From=%2B{phone[1:]}&Body=2",
                       headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 200 and "<Response>" in resp.text
    client.post("/reset")
