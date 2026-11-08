"""Put the demo caller (a consenting teammate's phone, handed to the judge) on the demo home.

    uv run python -m scripts.demo_caller --name "Maria" --phone +12145550123
    uv run python -m scripts.demo_caller --show

Writes data/demo_households.yaml (gitignored) and reseeds, so the demo home is a live household in
wave 1 of the next run. Trial phone accounts can usually only call verified numbers: verify it first.
Reseeding resets the live database, so do this before the demo run, not during it.
"""
import argparse
import re
import subprocess
import sys

import yaml

from app.config import get_settings

DEFAULTS = {"address": "1412 Oak St, Richardson, TX", "age": 81, "lives_alone": True,
            "medical_device": "oxygen concentrator", "has_car": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name")
    parser.add_argument("--phone", help="E.164, e.g. +12145550123")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--no-seed", action="store_true", help="only write the file")
    args = parser.parse_args()
    path = get_settings().data_dir / "demo_households.yaml"
    homes = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    homes = homes or []
    if args.show or not (args.name or args.phone):
        if not homes:
            print(f"No demo caller yet ({path} doesn't exist).")
        for h in homes:
            print(f"{h.get('name')}  {h.get('phone')}  {h.get('address', DEFAULTS['address'])}")
        return 0
    if args.phone and not re.fullmatch(r"\+[1-9]\d{7,14}", args.phone):
        print("--phone must be E.164, like +12145550123")
        return 1
    first = homes[0] if homes else dict(DEFAULTS)
    if args.name:
        first["name"] = args.name
    if args.phone:
        first["phone"] = args.phone
    if not first.get("name") or not first.get("phone"):
        print("The first time, give both --name and --phone.")
        return 1
    homes = [first, *homes[1:]]
    path.write_text("# Demo callers: real, consenting phones only. Gitignored.\n" + yaml.safe_dump(homes, sort_keys=False),
                    encoding="utf-8")
    print(f"Demo caller: {first['name']} {first['phone']} at {first.get('address', DEFAULTS['address'])}")
    if args.no_seed:
        return 0
    return subprocess.call([sys.executable, "-m", "scripts.seed"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
