"""The run's clock. Every timer in a run (call lengths, crew jobs, status updates, follow-ups) goes
through one of these, so the same engine code runs a sped-up live demo or an instant simulated test.

- RealClock: real time, sped up. One real second is `speed` seconds on the run's clock.
- SimClock: jumps straight from one scheduled callback to the next. Nothing waits in real time,
  and callbacks run one at a time in time order, so a simulated run is fully repeatable.

Times are timezone-aware UTC. Callbacks may be plain functions or coroutine functions.
"""
from __future__ import annotations

import asyncio
import heapq
import inspect
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from app.db import utcnow

log = logging.getLogger(__name__)

Callback = Callable[[], Awaitable[None] | None]

# Every simulated run starts here (5:00 pm Central, when the storm hits), so runs are repeatable.
SIM_START = datetime(2026, 11, 7, 23, 0, tzinfo=timezone.utc)


class Timer:
    """A scheduled callback. cancel() stops it if it hasn't run yet."""

    def __init__(self, when: datetime) -> None:
        self.when = when
        self.cancelled = False
        self._handle: asyncio.TimerHandle | None = None

    def cancel(self) -> None:
        self.cancelled = True
        if self._handle is not None:
            self._handle.cancel()


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError

    def call_at(self, when: datetime, fn: Callback) -> Timer:
        raise NotImplementedError

    def call_later(self, seconds: float, fn: Callback) -> Timer:
        return self.call_at(self.now() + timedelta(seconds=max(seconds, 0.0)), fn)

    def cancel_all(self) -> None:
        raise NotImplementedError


class SimClock(Clock):
    def __init__(self, start: datetime = SIM_START) -> None:
        self._now = start
        self._queue: list[tuple[datetime, int, Timer, Callback]] = []
        self._seq = itertools.count()
        self.callbacks_run = 0

    def now(self) -> datetime:
        return self._now

    def call_at(self, when: datetime, fn: Callback) -> Timer:
        when = max(when, self._now)
        timer = Timer(when)
        heapq.heappush(self._queue, (when, next(self._seq), timer, fn))
        return timer

    async def run(self, max_callbacks: int = 5_000_000) -> None:
        """Run every scheduled callback in time order (they may schedule more) until none are left.
        Equal times run in the order they were scheduled."""
        while self._queue:
            when, _, timer, fn = heapq.heappop(self._queue)
            if timer.cancelled:
                continue
            self._now = when
            result = fn()
            if inspect.isawaitable(result):
                await result
            self.callbacks_run += 1
            if self.callbacks_run >= max_callbacks:
                raise RuntimeError("simulated run did not settle (too many callbacks)")

    def cancel_all(self) -> None:
        for _, _, timer, _ in self._queue:
            timer.cancel()
        self._queue.clear()


class RealClock(Clock):
    def __init__(self, speed: float = 1.0, start: datetime | None = None) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = speed
        self._start = start or utcnow()
        self._t0 = time.monotonic()
        self._timers: set[Timer] = set()
        self._tasks: set[asyncio.Task] = set()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=(time.monotonic() - self._t0) * self.speed)

    def call_at(self, when: datetime, fn: Callback) -> Timer:
        timer = Timer(when)
        delay = max(0.0, (when - self.now()).total_seconds() / self.speed)
        timer._handle = asyncio.get_running_loop().call_later(delay, self._fire, timer, fn)
        self._timers.add(timer)
        return timer

    def _fire(self, timer: Timer, fn: Callback) -> None:
        if timer.cancelled:
            self._timers.discard(timer)
            return
        # asyncio may fire up to one clock tick early (about 15 ms on Windows); never run early.
        remaining = (timer.when - self.now()).total_seconds() / self.speed
        if remaining > 0.0005:
            timer._handle = asyncio.get_running_loop().call_later(remaining, self._fire, timer, fn)
            return
        self._timers.discard(timer)
        try:
            result = fn()
        except Exception:
            log.exception("clock callback failed")
            return
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("clock callback failed", exc_info=task.exception())

    def cancel_all(self) -> None:
        for timer in list(self._timers):
            timer.cancel()
        self._timers.clear()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
