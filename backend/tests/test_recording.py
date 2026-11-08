"""M10: record a run and replay it."""
import shutil
import time

from fastapi.testclient import TestClient

from app.city import build_city, write_city
from app.config import REPO_ROOT
from app.geo import WarningArea
from app.main import create_app


def wait_for(fn, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_record_then_replay_restores_the_run(settings):
    shutil.copy(REPO_ROOT / "data" / "warning_area.geojson", settings.data_dir / "warning_area.geojson")
    write_city(build_city(WarningArea.load(settings.data_dir / "warning_area.geojson"), seed=3, n=120),
               settings.live_db_path)
    with TestClient(create_app(settings)) as client:
        assert client.post("/recordings/save", params={"name": "nothing"}).status_code == 404
        client.post("/alerts/demo", params={"speed": 1000})
        done = wait_for(lambda: (s := client.get("/state").json())["run"] and not s["run"]["active"] and s)
        saved = client.post("/recordings/save", params={"name": "good run!"}).json()
        assert saved["name"] == "good-run" and saved["events"] > 0
        assert client.get("/recordings").json()["recordings"] == [{"name": "good-run", "events": saved["events"]}]

        client.post("/reset")
        assert all(h["status"] == "not_called" for h in client.get("/state").json()["households"])
        assert client.post("/recordings/missing/play").status_code == 404
        started = client.post("/recordings/good-run/play", params={"speed": 1000}).json()
        assert started["events"] == saved["events"]
        replayed = wait_for(lambda: (s := client.get("/state").json())["counts"]["reached"] == done["counts"]["reached"] and s)
        assert {h["id"]: h["status"] for h in replayed["households"]} == {h["id"]: h["status"] for h in done["households"]}
        assert replayed["warning_area"] is not None
        client.post("/reset")
