"""M3 engine: simulated runs on the scripted channel."""
import time
from collections import defaultdict
from datetime import datetime

import pytest
from sqlmodel import select

from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.db import Database
from app.engine.core import ALLOWED
from app.engine.runs import run_sim
from app.geo import WarningArea
from app.models import Action, CallPolicy, Event, Household, Resource, ResourceType, Status

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


async def test_a_sim_run_finishes_fast_and_every_affected_home_ends_final(base_db, tmp_path):
    started = time.perf_counter()
    summary = await run_sim(base_db, tmp_path / "run.db", AREA, lines=50)
    assert time.perf_counter() - started < 30
    assert summary["affected"] > 0 and summary["called"] == summary["affected"]
    affected = [h for h in read(tmp_path / "run.db", Household) if AREA.contains(h.lat, h.lng)]
    assert not any(h.status in (Status.NOT_CALLED, Status.CALLING) for h in affected)
    assert summary["help_sent"] > 0 and summary["true_urgent_helped"] > 0


async def test_same_seed_same_result(base_db, tmp_path):
    first = await run_sim(base_db, tmp_path / "a.db", AREA, lines=40)
    second = await run_sim(base_db, tmp_path / "b.db", AREA, lines=40)
    assert first == second
    key = lambda a: (a.household_id, a.type, a.resource_id, a.status, a.created_at)  # noqa: E731
    assert sorted(map(key, read(tmp_path / "a.db", Action))) == sorted(map(key, read(tmp_path / "b.db", Action)))


async def test_every_status_change_is_allowed(base_db, tmp_path):
    await run_sim(base_db, tmp_path / "run.db", AREA, lines=40)
    changes = [e for e in read(tmp_path / "run.db", Event) if e.type == "household.status"]
    assert changes
    for e in changes:
        assert Status(e.payload["status"]) in ALLOWED[Status(e.payload["previous"])], e.payload


async def test_resources_are_never_double_booked(base_db, tmp_path):
    await run_sim(base_db, tmp_path / "run.db", AREA, lines=100)
    resources = {r.id: r for r in read(tmp_path / "run.db", Resource)}
    jobs, beds = defaultdict(list), defaultdict(int)
    for a in read(tmp_path / "run.db", Action):
        if a.resource_id is None:
            continue
        if resources[a.resource_id].type is ResourceType.SHELTER:
            beds[a.resource_id] += a.details["beds"]
        else:
            jobs[a.resource_id].append((datetime.fromisoformat(a.details["start_at"]),
                                        datetime.fromisoformat(a.details["done_at"])))
    assert jobs, "expected some crew or adjuster jobs"
    for intervals in jobs.values():
        intervals.sort()
        for (_, done), (start, _) in zip(intervals, intervals[1:]):
            assert start >= done
    for shelter_id, used in beds.items():
        assert used <= resources[shelter_id].capacity


async def test_risk_ranking_gets_urgent_homes_help_sooner_than_list_order(base_db, tmp_path):
    ranked = await run_sim(base_db, tmp_path / "ranked.db", AREA, policy=CallPolicy.RISK_RANKED, lines=20)
    listed = await run_sim(base_db, tmp_path / "listed.db", AREA, policy=CallPolicy.LIST_ORDER, lines=20)
    assert ranked["median_minutes_to_help_urgent"] < listed["median_minutes_to_help_urgent"]
