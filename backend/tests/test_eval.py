"""M9: evaluation, replay of simulated calls, keyword baseline."""
import json

import pytest
from fastapi.testclient import TestClient

from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.engine.channels import CallResult, NeedReport
from app.eval.evaluate import evaluate, render_markdown, write_results
from app.eval.keywords import keyword_urgency
from app.main import create_app
from app.models import CallOutcome, Channel, NeedCategory, Persona, Urgency

AREA_FILE = REPO_ROOT / "data" / "warning_area.geojson"


def test_keywords_miss_a_downplayed_emergency_but_catch_plain_ones():
    downplayed = [{"role": "resident", "line": "Oh I'm fine, honey. The oxygen machine just keeps beeping."}]
    assert keyword_urgency(downplayed) is Urgency.P4
    assert keyword_urgency([{"role": "resident", "line": "I smell gas in the kitchen"}]) is Urgency.P1
    assert keyword_urgency([{"role": "resident", "line": "The power is out and my CPAP is on battery"}]) is Urgency.P2
    assert keyword_urgency([]) is Urgency.P4


@pytest.fixture
def city(settings):
    import shutil

    shutil.copy(AREA_FILE, settings.data_dir / "warning_area.geojson")
    from app.city import base_db_path
    from app.geo import WarningArea

    c = build_city(WarningArea.load(AREA_FILE), seed=5, n=300)
    write_city(c, base_db_path(settings))
    return c


async def test_scripted_evaluation_compares_both_orders(settings, city):
    r = await evaluate(settings, lines=20)
    assert r["source"]["calls"] == "scripted" and r["triage_compare"] is None and r["traps"] == []
    ranked, listed = r["runs"]["risk_ranked"], r["runs"]["list_order"]
    assert ranked["urgent_found"] == listed["urgent_found"]  # same calls, same decisions
    assert ranked["median_minutes_to_help"] <= listed["median_minutes_to_help"]
    assert ranked["coverage_pct"] == 100.0
    assert len(ranked["curve"]) == len(listed["curve"]) and ranked["curve"][-1][1] == ranked["true_urgent_helped"]
    assert any("scripted" in n for n in r["notes"])
    assert "| Urgent found (%)" in render_markdown(r)


async def test_replayed_simulated_calls_feed_traps_and_the_side_by_side(settings, city, monkeypatch):
    truths = {t.household_id: t for t in city.truths}
    downplayer = next(t for t in city.truths if t.persona_style is Persona.DOWNPLAYS)
    outcomes = {}
    for t in city.truths:
        urgency = t.true_urgency
        transcript = [{"role": "agent", "line": "Are you safe right now?"},
                      {"role": "resident", "line": "I'm fine, honey. My oxygen machine keeps beeping." if t is downplayer
                       else "We're okay here."}]
        outcomes[t.household_id] = CallResult(t.household_id, 1, Channel.SIM, CallOutcome.COMPLETED, urgency=urgency,
                                              needs=[NeedReport(NeedCategory.POWER, "oxygen on battery", urgency)] if urgency != Urgency.P4 else [],
                                              summary="Simulated call.", transcript=transcript, duration_s=90)
    monkeypatch.setattr("app.eval.evaluate.load_simulated", lambda s, seed: (outcomes, {"spec_hash": "test"}))
    r = await evaluate(settings, lines=20)
    assert r["source"]["calls"] == "simulated" and r["source"]["scripted_households"] == 0
    assert r["traps"] and r["triage_compare"]["households"] > 0
    assert r["trap_case"]["household_id"] == downplayer.household_id
    assert r["trap_case"]["keyword_said"].startswith("P4") and "fine" in r["trap_case"]["quote"]
    assert r["triage_compare"]["keywords"]["urgent_found_pct"] < r["triage_compare"]["nemotron"]["urgent_found_pct"]


async def test_results_endpoint(settings, city, tmp_path):
    r = await evaluate(settings, lines=20)
    latest, _ = write_results(r, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.results_path = tmp_path / "missing.json"
        assert client.get("/results").status_code == 404
        app.state.results_path = latest
        assert client.get("/results").json()["runs"]["risk_ranked"]["affected"] > 0
