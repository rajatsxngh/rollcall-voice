"""M9: RollCall vs list order, scored against the hidden truth. Writes results/latest.json and RESULTS.md.

    uv run python -m scripts.run_eval                 # replays simulated calls if stored, else scripted
    uv run python -m scripts.run_eval --nemotron      # Nemotron makes the decisions (needs keys; cached)
    uv run python -m scripts.run_eval --scripted      # ignore stored simulated calls
    uv run python -m scripts.run_eval --lines 30
"""
import argparse
import asyncio
import sys
import time

from app.config import get_settings
from app.eval.evaluate import evaluate, render_markdown, write_results
from app.llm import LLMClient


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lines", type=int, default=100)
    parser.add_argument("--nemotron", action="store_true", help="Nemotron triage, plan and re-plans")
    parser.add_argument("--scripted", action="store_true", help="don't use stored simulated calls")
    args = parser.parse_args()
    settings = get_settings()
    llm = LLMClient(settings) if args.nemotron else None
    if llm is not None and not llm.providers:
        print("--nemotron needs NVIDIA_API_KEYS or OPENROUTER_API_KEY in .env")
        return 1
    started = time.perf_counter()
    try:
        results = await evaluate(settings, lines=args.lines, llm=llm, use_simulated=not args.scripted)
    finally:
        if llm is not None:
            await llm.aclose()
    latest, md = write_results(results)
    print(render_markdown(results))
    print(f"Done in {time.perf_counter() - started:.0f} s. Wrote {latest} and {md}.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
