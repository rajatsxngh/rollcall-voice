"""Simulated runs on the scripted channel: the whole city in seconds, on a simulated clock.

    uv run python -m scripts.run_sim                          # risk-ranked, 100 lines
    uv run python -m scripts.run_sim --policy list_order --lines 50
    uv run python -m scripts.run_sim --compare                # RollCall vs list order, side by side

Each run gets its own file in data/runs/ (copied from data/base.db; run scripts.seed first).
The scripted channel answers straight from the hidden truth, so these numbers show the engine and
the call order, not how well the voice agent listens (that's M7 and M9).
"""
import argparse
import asyncio
import sys
import time

from app.city import base_db_path
from app.config import get_settings
from app.engine.runs import run_sim
from app.geo import WarningArea
from app.models import CallPolicy

ROWS = [
    ("affected", "homes in the warning area"),
    ("calls", "calls placed"),
    ("reached", "reached"),
    ("urgent", "urgent or emergency"),
    ("help_sent", "homes with help on the way"),
    ("not_reached", "not reached yet"),
    ("true_urgent", "truly urgent (P1+P2) homes"),
    ("true_urgent_helped", "...with help on the way"),
    ("median_minutes_to_help_urgent", "median minutes until an urgent home had help"),
    ("minutes_until_all_reached_urgent_helped", "minutes until every reached urgent home had help"),
    ("minutes", "minutes until the first pass ended"),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", choices=[p.value for p in CallPolicy], default=CallPolicy.RISK_RANKED.value)
    parser.add_argument("--lines", type=int, default=100)
    parser.add_argument("--compare", action="store_true", help="run both policies")
    args = parser.parse_args()

    settings = get_settings()
    base = base_db_path(settings)
    if not base.exists():
        print("No seeded city. Run: uv run python -m scripts.seed")
        return 1
    area = WarningArea.load(settings.data_dir / "warning_area.geojson")
    policies = list(CallPolicy) if args.compare else [CallPolicy(args.policy)]
    results = {}
    for policy in policies:
        started = time.perf_counter()
        out = settings.runs_dir / f"sim_{policy.value}.db"
        results[policy] = await run_sim(base, out, area, policy=policy, lines=args.lines)
        print(f"{policy.value}: finished in {time.perf_counter() - started:.1f} s -> {out}")

    print(f"\n{'':50}" + "".join(f"{p.value:>14}" for p in policies))
    for key, label in ROWS:
        print(f"{label:50}" + "".join(f"{str(results[p].get(key)):>14}" for p in policies))
    print(f"\n{args.lines} phone lines; simulated calls take about 2 minutes each.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
