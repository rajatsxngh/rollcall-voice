import pytest
from fastapi.testclient import TestClient

from app.events import EventType
from app.main import create_app
from app.models import Status


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "live.db"
    assert body["llm"] == {"nvidia_keys": 0, "openrouter": False}


def test_meta_covers_every_status_and_event_type(client):
    meta = client.get("/meta").json()
    assert [s["value"] for s in meta["statuses"]] == [s.value for s in Status]
    assert all(s["color"].startswith("#") for s in meta["statuses"])
    assert {e["value"] for e in meta["event_types"]} == {t.value for t in EventType}
    assert meta["display_tz"] == "America/Chicago"


def test_emitted_events_are_stored_and_listed_in_order(client):
    bus = client.app.state.bus
    first = bus.emit(EventType.AGENT_NOTE, {"text": "one"})
    second = bus.emit("household.status", {"status": Status.SAFE}, household_id=7)
    listed = client.get("/events", params={"since": first["id"]}).json()
    assert [e["id"] for e in listed["events"]] == [second["id"]]
    assert listed["last_id"] == second["id"]
    event = listed["events"][0]
    assert event["payload"] == {"status": "safe"}
    assert event["household_id"] == 7
    assert event["time"].endswith("Z")


def test_events_filter_by_household(client):
    bus = client.app.state.bus
    bus.emit(EventType.AGENT_NOTE, {"text": "other"}, household_id=1)
    mine = bus.emit(EventType.AGENT_NOTE, {"text": "mine"}, household_id=2)
    listed = client.get("/events", params={"household_id": 2}).json()["events"]
    assert [e["id"] for e in listed] == [mine["id"]]


def test_unknown_event_type_is_rejected(client):
    with pytest.raises(ValueError):
        client.app.state.bus.emit("not.a.type", {})
    assert client.post("/debug/emit", json={"type": "not.a.type"}).status_code == 422


def test_websocket_replays_history_then_streams_live(client):
    bus = client.app.state.bus
    before = bus.emit(EventType.AGENT_NOTE, {"text": "before connect"})
    with client.websocket_connect(f"/ws?since={before['id'] - 1}") as ws:
        assert ws.receive_json()["id"] == before["id"]
        sent = client.post("/debug/emit", json={"type": "debug.ping", "payload": {"n": 1}}).json()
        live = ws.receive_json()
        assert live["id"] == sent["id"]
        assert live["payload"] == {"n": 1}


def test_websocket_without_since_only_streams_new_events(client):
    bus = client.app.state.bus
    bus.emit(EventType.AGENT_NOTE, {"text": "old"})
    with client.websocket_connect("/ws") as ws:
        new = bus.emit(EventType.AGENT_NOTE, {"text": "new"})
        assert ws.receive_json()["id"] == new["id"]


def test_debug_emit_can_be_turned_off(settings):
    off = settings.model_copy(update={"debug_endpoints": False})
    with TestClient(create_app(off)) as client:
        assert client.post("/debug/emit", json={"type": "debug.ping"}).status_code == 404
