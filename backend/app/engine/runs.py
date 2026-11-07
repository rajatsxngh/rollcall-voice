"""Running the engine: the one live run behind POST /alerts/demo, and simulated runs for tests.

Design decision 6: every run has its own database file. The live run uses data/runs/live.db;
a simulated run copies the seeded base into its own file and runs on a SimClock, so it finishes in
seconds and gives the same result every time for the same seed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import delete, update
from sqlmodel import select

from app.city import copy_database
from app.clock import RealClock, SimClock
from app.config import Settings
from app.db import Database, iso_utc, utcnow
from app.engine.channels import ScriptedChannel
from app.engine.core import Engine
from app.events import EventBus, EventType
from app.geo import WarningArea
from app.models import Action, Call, CallPolicy, HiddenTruth, Household, Need, Resource, RunInfo, Status

log = logging.getLogger(__name__)


def load_truths(db: Database) -> dict[int, HiddenTruth]:
    with db.session() as session:
        return {t.household_id: t for t in session.exec(select(HiddenTruth)).all()}


def city_seed(db: Database) -> int:
    with db.session() as session:
        info = session.get(RunInfo, 1)
    return info.seed if info else 42


def reset_run_data(db: Database) -> None:
    """Back to before the alert: nobody called, no needs, actions or calls, every resource free.
    Events are kept: the log is history."""
    with db.session() as session:
        session.execute(delete(Need))
        session.execute(delete(Action))
        session.execute(delete(Call))
        session.execute(update(Household).values(status=Status.NOT_CALLED, wave=None))
        session.execute(update(Resource).values(used=0, free_at=None))
        session.commit()


def run_needs_reset(db: Database) -> bool:
    with db.session() as session:
        return session.exec(
            select(Household.id).where(Household.status != Status.NOT_CALLED).limit(1)
        ).first() is not None


def recover_after_restart(db: Database, bus: EventBus) -> int:
    """A backend restart kills the run in memory. Calls that were in progress can never finish,
    so put those households back to "not called" instead of leaving them stuck in "calling"."""
    with db.session() as session:
        stuck = session.exec(select(Household).where(Household.status == Status.CALLING)).all()
        for h in stuck:
            h.status = Status.NOT_CALLED
            session.add(h)
        session.commit()
    if stuck:
        bus.emit(EventType.AGENT_NOTE, {
            "text": (f"The backend restarted during a run: {len(stuck)} calls in progress were dropped. "
                     "Press Reset to start over."),
            "reason": "restart",
        })
    return len(stuck)


class LiveRun:
    """The single live run: started by POST /alerts/demo, cleared by POST /reset."""

    def __init__(self, db: Database, bus: EventBus, settings: Settings) -> None:
        self.db, self.bus, self.settings = db, bus, settings
        self.engine: Engine | None = None
        self.clock: RealClock | None = None

    @property
    def active(self) -> bool:
        return self.engine is not None and not self.engine.finished

    def info(self) -> dict[str, Any] | None:
        if self.engine is None or self.clock is None:
            return None
        return {
            "active": self.active,
            "engine": self.engine.triage.source,
            "policy": self.engine.policy.value,
            "lines": self.engine.lines,
            "speed": self.clock.speed,
            "started_at": iso_utc(self.engine.started_at),
            "finished_at": iso_utc(self.engine.finished_at),
            "clock": iso_utc(self.clock.now()),
        }

    async def start(self, speed: float, lines: int, policy: CallPolicy) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("a run is already in progress")
        self.stop()
        clock = RealClock(speed)
        self.bus.now = clock.now  # events carry the run's clock, so "+N min" is right at any speed
        area = WarningArea.load(self.settings.data_dir / "warning_area.geojson")
        channel = ScriptedChannel(clock, load_truths(self.db), seed=city_seed(self.db))
        self.clock = clock
        self.engine = Engine(db=self.db, bus=self.bus, clock=clock, area=area, channel=channel,
                             policy=policy, lines=lines)
        await self.engine.start()
        return self.info() or {}

    def stop(self) -> None:
        """Stop every timer of the current run (if any) and go back to wall-clock event times."""
        if self.clock is not None:
            self.clock.cancel_all()
        self.bus.now = utcnow
        self.clock = None
        self.engine = None

    def reset(self) -> None:
        self.stop()
        reset_run_data(self.db)
        self.bus.emit(EventType.RUN_RESET, {})


async def run_sim(
    base: Path,
    out: Path,
    area: WarningArea,
    *,
    policy: CallPolicy | str = CallPolicy.RISK_RANKED,
    lines: int = 100,
    seed: int | None = None,
) -> dict[str, Any]:
    """One simulated run in its own database file (copied from the seeded base). Returns the summary."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{out}{suffix}").unlink(missing_ok=True)
    copy_database(base, out)
    db = Database(out, durable=False)
    try:
        clock = SimClock()
        bus = EventBus(db, now=clock.now)
        truths = load_truths(db)
        channel = ScriptedChannel(clock, truths, seed=seed if seed is not None else city_seed(db))
        engine = Engine(db=db, bus=bus, clock=clock, area=area, channel=channel, policy=policy, lines=lines)
        await engine.start()
        await clock.run()
        return engine.summary(truths)
    finally:
        db.dispose()
