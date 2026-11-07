"""M7 batch runner: simulated residents talk to the shared voice agent; each call is stored in
data/cache/sim_outcomes.db for replay through the engine (M9).

Run it in tiers. Each tier skips households already stored for the current voice spec and models,
so rerunning a command resumes it (after a crash or Ctrl+C) and a finished tier reruns in seconds.

    uv run python -m scripts.sim_calls --traps --dry-run      # first resident prompt + household list
    uv run python -m scripts.sim_calls --traps                # the 30 trap personas
    uv run python -m scripts.sim_calls --pilot 200            # all true P1/P2, then a sample
    uv run python -m scripts.sim_calls --all                  # every household in the warning area
    uv run python -m scripts.sim_calls --all --summary        # just the summary of what's stored
    uv run python -m scripts.sim_calls show 123               # one stored transcript

Options: --db (default data/base.db; the city seed is read from its run_info), --concurrency
(default 8 calls at once), --max-turns (agent turns per call, default 10), --area (warning area
GeoJSON, default data/warning_area.geojson).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from app.agent import voice_spec
from app.config import get_settings
from app.geo import WarningArea
from app.llm import LLMClient
from app.sim.batch import City, Progress, format_transcript, load_city, run_batch, select_households, summarize
from app.sim.conversation import MAX_AGENT_TURNS
from app.sim.persona import resident_prompt
from app.sim.store import OutcomeStore, SimKey, configured_providers


def parse_args(argv: list[str]) -> argparse.Namespace:
    settings = get_settings()
    if argv and argv[0] == "show":
        p = argparse.ArgumentParser(prog="sim_calls show", description="Print one stored transcript.")
        p.add_argument("command")
        p.add_argument("household_id", type=int)
        p.add_argument("--db", type=Path, default=settings.data_dir / "base.db")
        p.add_argument("--any-spec", action="store_true",
                       help="show the newest stored call even if it was made with another spec or models")
        return p.parse_args(argv)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tier = p.add_mutually_exclusive_group(required=True)
    tier.add_argument("--traps", action="store_true", help="the 30 trap personas")
    tier.add_argument("--pilot", type=int, metavar="N", help="N homes: all true P1/P2 first, then a sample")
    tier.add_argument("--all", action="store_true", help="every household inside the warning area")
    p.add_argument("--db", type=Path, default=settings.data_dir / "base.db")
    p.add_argument("--area", type=Path, default=settings.data_dir / "warning_area.geojson")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--max-turns", type=int, default=MAX_AGENT_TURNS)
    p.add_argument("--dry-run", action="store_true", help="print the first prompt and the list; no model calls")
    p.add_argument("--summary", action="store_true", help="print the summary of stored results; no model calls")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    args.command = "run"
    return args


def pick(args: argparse.Namespace, city: City) -> tuple[str, list[int]]:
    if args.traps:
        return "traps", select_households(city, "traps")
    area = WarningArea.load(args.area)
    if args.pilot is not None:
        return f"pilot {args.pilot}", select_households(city, "pilot", area, pilot_n=args.pilot)
    return "all", select_households(city, "all", area)


def dry_run(city: City, ids: list[int], key: SimKey) -> None:
    print(f"Spec {key.spec_hash} | voice {key.voice_model} | resident {key.resident_model} | seed {key.seed}")
    talking = [i for i in ids if city.truths[i].answers_on_attempt is not None]
    if talking:
        h = city.households[talking[0]]
        print(f"\n--- Resident prompt for household {h.id} ({city.truths[h.id].persona_style.value}) ---")
        print(resident_prompt(h, city.truths[h.id]))
        print(f"\n--- Agent opening line ---\n{voice_spec.render(voice_spec.FIRST_MESSAGE, name=h.name)}")
    print(f"\n--- {len(ids)} households ---")
    for i in ids:
        h, t = city.households[i], city.truths[i]
        answers = "never answers" if t.answers_on_attempt is None else f"answers on try {t.answers_on_attempt}"
        print(f"{i:>5}  {h.name:<24} {t.persona_style.value:<16} true {t.true_urgency.value}  {answers}"
              f"  risk {h.risk_score:.2f}")


def show(args: argparse.Namespace) -> int:
    settings = get_settings()
    city = load_city(args.db)
    store = OutcomeStore.open(settings)
    try:
        key = SimKey.current(city.seed, settings)
        stored = store.load(key, args.household_id)
        if stored is None:
            latest = store.latest(args.household_id, city.seed)
            if latest is None or not args.any_spec:
                hint = " (an older spec or model has one: add --any-spec)" if latest else ""
                print(f"No stored call for household {args.household_id} with the current spec{hint}.")
                return 1
            key, stored = latest
        print(format_transcript(city.households.get(args.household_id), city.truths.get(args.household_id),
                                key, stored))
    finally:
        store.close()
    return 0


class Display:
    """One progress line, rewritten in place at most once a second."""

    def __init__(self) -> None:
        self._last = 0.0

    def __call__(self, progress: Progress, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last >= 1.0 or progress.done + progress.failed + progress.skipped >= progress.total:
            self._last = now
            sys.stderr.write("\r" + progress.line().ljust(110))
            sys.stderr.flush()


async def run(args: argparse.Namespace, city: City, ids: list[int], key: SimKey, llm: LLMClient,
              store: OutcomeStore) -> Progress:
    display = Display()
    try:
        progress = await run_batch(llm, store, key, city, ids, concurrency=args.concurrency,
                                   max_agent_turns=args.max_turns, on_progress=display)
        display(progress, force=True)
        return progress
    finally:
        sys.stderr.write("\n")
        await llm.aclose()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.command == "show":
        return show(args)

    settings = get_settings()
    city = load_city(args.db)
    label, ids = pick(args, city)
    key = SimKey.current(city.seed, settings)
    if args.dry_run:
        dry_run(city, ids, key)
        return 0

    store = OutcomeStore.open(settings)
    try:
        already = len(store.stored_ids(key) & set(ids))
        print(f"Tier {label}: {len(ids)} households, {already} already stored | spec {key.spec_hash} |"
              f" voice {key.voice_model} | resident {key.resident_model} | seed {key.seed}")
        if not args.summary and already < len(ids):
            if not configured_providers(settings):
                print("No model provider configured: set NVIDIA_API_KEYS and/or OPENROUTER_API_KEY in .env")
                return 2
            llm = LLMClient(settings)
            if args.concurrency > settings.llm_max_concurrency:
                print(f"Note: LLM_MAX_CONCURRENCY is {settings.llm_max_concurrency} per provider, so at most"
                      " that many model requests run at once whatever --concurrency says.")
            try:
                asyncio.run(run(args, city, ids, key, llm, store))
            except KeyboardInterrupt:
                print("\nStopped. Finished calls are stored; rerun the same command to resume.")
        print()
        print(summarize(city, store.load_all(key), ids))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
