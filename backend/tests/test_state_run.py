"""GET /state and the stand-in run (POST /alerts/demo, POST /reset)."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.geo import WarningArea
from app.main import create_app


@pytest.fixture
def city_client(settings):
    data = settings.data_dir
    shutil.copy(REPO_ROOT / "data" / "warning_area.geojson", data / "warning_area.geojson")
    area = WarningArea.load(data / "warning_area.geojson")
    write_city(build_city(area, seed=3, n=150), settings.live_db_path)
    with TestClient(create_app(settings)) as client:
        yield client


def wait_until(client, done, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/state").json()
        if done(state):
            return state
        time.sleep(0.1)
    raise AssertionError(f"timed out; last counts: {state['counts']}")


def test_state_before_any_alert(city_client):
    state = city_client.get("/state").json()
    assert len(state["households"]) == 150
    assert state["warning_area"] is None
    assert not any(h["affected"] for h in state["households"])
    assert "phone" not in state["households"][0]
    assert state["counts"]["affected"] == 0
    assert {r["type"] for r in state["resources"]} == {"crew", "shelter", "adjuster", "generator"}


def test_demo_run_then_reset(city_client):
    assert city_client.post("/alerts/demo", params={"speed": 1000}).json()["started"]
    state = wait_until(city_client, lambda s: s["counts"]["affected"] and s["counts"]["called"] == s["counts"]["affected"]
                       and not any(h["status"] == "calling" for h in s["households"]))
    counts = state["counts"]
    assert state["warning_area"]["geometry"]["type"] == "Polygon"
    assert counts["reached"] > 0 and counts["not_reached"] < counts["affected"]
    assert counts["help_sent"] == len(state["helped_household_ids"]) > 0

    events = city_client.get("/events", params={"limit": 5000}).json()["events"]
    types = [e["type"] for e in events]
    assert types[0] == "alert.received" and "plan.created" in types and "action.created" in types
    bookkeeping = {"open_claim", "schedule_followup", "send_text"}
    assert all("text" in e["payload"] for e in events
               if e["type"] == "action.created" and e["payload"]["type"] not in bookkeeping)
    assert state["run"]["engine"] == "rules" and not state["run"]["active"]
    assert any(e["type"] == "agent.note" and "No model key" in e["payload"]["text"] for e in events)

    assert city_client.post("/reset").json()["reset"]
    after = city_client.get("/state").json()
    assert after["warning_area"] is None
    assert all(h["status"] == "not_called" for h in after["households"])
    assert after["counts"]["help_sent"] == 0


def test_second_alert_while_running_is_refused(city_client):
    city_client.post("/alerts/demo", params={"speed": 1})
    assert city_client.post("/alerts/demo").status_code == 409
    city_client.post("/reset")


def test_event_times_follow_the_run_clock(city_client):
    city_client.post("/alerts/demo", params={"speed": 600})
    state = wait_until(city_client, lambda s: s["run"] and not s["run"]["active"])
    events = city_client.get("/events", params={"limit": 5000}).json()["events"]
    alert = next(e for e in events if e["type"] == "alert.received")
    done = next(e for e in events if e["type"] == "agent.note" and "First pass done" in e["payload"]["text"])
    from datetime import datetime

    elapsed = (datetime.fromisoformat(done["time"].replace("Z", "+00:00"))
               - datetime.fromisoformat(alert["time"].replace("Z", "+00:00"))).total_seconds()
    assert elapsed > 60  # simulated minutes passed, though the test only took a moment
    assert state["counts"]["called"] == state["counts"]["affected"]
    city_client.post("/reset")


def test_restart_puts_stuck_calls_back(settings):
    data = settings.data_dir
    shutil.copy(REPO_ROOT / "data" / "warning_area.geojson", data / "warning_area.geojson")
    area = WarningArea.load(data / "warning_area.geojson")
    city = build_city(area, seed=3, n=50)
    for h in city.households[:3]:
        h.status = "calling"
    write_city(city, settings.live_db_path)
    with TestClient(create_app(settings)) as client:
        state = client.get("/state").json()
        assert not any(h["status"] == "calling" for h in state["households"])
        notes = [e for e in client.get("/events").json()["events"] if e["type"] == "agent.note"]
        assert notes and "restarted" in notes[-1]["payload"]["text"]
