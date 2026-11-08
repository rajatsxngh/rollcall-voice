"""Inbound calls carry no household id: the caller's number finds the household."""
from app.db import Database
from app.models import Household
from app.routers.vapi import resolve_household


def test_caller_number_finds_the_live_home_and_metadata_wins(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_all()
    with db.session() as s:
        s.add(Household(id=1, name="Fake", phone="+15550000001", address="a", lat=32.9, lng=-96.7, age=40))
        s.add(Household(id=2, name="Judge", phone="+12145551234", address="1412 Oak St", lat=32.9, lng=-96.7,
                        age=81, live=True))
        s.commit()
    inbound = {"call": {"type": "inboundPhoneCall", "customer": {"number": "+1 (214) 555-1234"}}}
    assert resolve_household(db, inbound) == 2
    assert resolve_household(db, {"call": {"customer": {"number": "+19999999999"}}}) is None
    outbound = {"call": {"customer": {"number": "+12145551234"},
                         "assistantOverrides": {"metadata": {"household_id": 1}}}}
    assert resolve_household(db, outbound) == 1
    db.dispose()


def test_assistant_request_fills_in_the_caller(settings, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    s = settings.model_copy(update={"vapi_assistant_id": "asst_123"})
    app = create_app(s)
    with TestClient(app) as client:
        with app.state.db.session() as session:
            session.add(Household(id=7, name="Maria Lopez", phone="+12145551234", address="1412 Oak St",
                                  lat=32.9, lng=-96.7, age=81, live=True))
            session.commit()
        body = {"message": {"type": "assistant-request", "call": {"customer": {"number": "+12145551234"}}}}
        reply = client.post("/vapi/webhook", json=body).json()
        assert reply == {"assistantId": "asst_123", "assistantOverrides": {
            "variableValues": {"name": "Maria"}, "metadata": {"household_id": 7}}}
        stranger = {"message": {"type": "assistant-request", "call": {"customer": {"number": "+13125550000"}}}}
        assert client.post("/vapi/webhook", json=stranger).json()["assistantOverrides"]["variableValues"]["name"] == "there"
