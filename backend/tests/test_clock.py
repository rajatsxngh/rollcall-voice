import asyncio
from datetime import timedelta

from app.clock import SIM_START, RealClock, SimClock


async def test_sim_clock_runs_in_time_order_skips_cancelled_and_allows_chaining():
    clock = SimClock()
    seen = []
    clock.call_later(10, lambda: seen.append(("b", clock.now())))
    dropped = clock.call_later(5, lambda: seen.append(("cancelled", clock.now())))
    clock.call_later(1, lambda: seen.append(("a", clock.now())))

    async def chained():
        seen.append(("c", clock.now()))
        clock.call_later(1, lambda: seen.append(("d", clock.now())))

    clock.call_later(20, chained)
    dropped.cancel()
    await clock.run()
    assert [name for name, _ in seen] == ["a", "b", "c", "d"]
    assert seen[-1][1] == SIM_START + timedelta(seconds=21)


async def test_sim_clock_keeps_scheduling_order_for_equal_times():
    clock = SimClock()
    seen = []
    for i in range(5):
        clock.call_later(3, lambda i=i: seen.append(i))
    await clock.run()
    assert seen == [0, 1, 2, 3, 4]


async def test_real_clock_runs_faster_than_real_time():
    clock = RealClock(speed=600)  # 60 clock seconds pass in 0.1 real seconds
    start = clock.now()
    fired = asyncio.Event()
    clock.call_later(60, fired.set)
    await asyncio.wait_for(fired.wait(), timeout=2)
    assert clock.now() - start >= timedelta(seconds=60)
    clock.cancel_all()


async def test_real_clock_cancel_all_stops_pending_timers():
    clock = RealClock(speed=600)
    fired = []
    clock.call_later(30, lambda: fired.append(1))
    clock.cancel_all()
    await asyncio.sleep(0.1)
    assert fired == []
