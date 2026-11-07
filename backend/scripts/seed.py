"""Build the fake city and load it into the live database.

    uv run python -m scripts.seed                  # 2,000 households, seed 42
    uv run python -m scripts.seed --seed 7 --households 500
    uv run python -m scripts.seed --no-live        # only write data/base.db

Writes data/base.db (the seeded base every run copies) and copies it to data/runs/live.db.
Real demo phones come from data/demo_households.yaml (gitignored; see the .example file).
"""
import argparse
import sys
import time

from app.city import base_db_path, build_city, copy_database, load_demo_households, write_city
from app.config import get_settings
from app.geo import WarningArea


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--households", type=int, default=2000)
    parser.add_argument("--no-live", action="store_true", help="don't copy into data/runs/live.db")
    args = parser.parse_args()

    settings = get_settings()
    started = time.perf_counter()
    area = WarningArea.load(settings.data_dir / "warning_area.geojson")
    demo = load_demo_households(settings.data_dir / "demo_households.yaml")
    city = build_city(area, seed=args.seed, n=args.households, demo=demo)
    base = base_db_path(settings)
    write_city(city, base)
    print(f"wrote {base} ({len(city.households)} households, {len(demo)} live demo, "
          f"{len(city.resources)} resources, seed {args.seed})")
    if not args.no_live:
        copy_database(base, settings.live_db_path)
        print(f"copied to {settings.live_db_path}. If the backend is running, reload the dashboard.")
    print(f"done in {time.perf_counter() - started:.1f} s. Check it: uv run python -m scripts.inspect_city")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
