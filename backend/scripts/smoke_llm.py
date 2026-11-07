"""Smoke test for the Nemotron client: real answers, real timings.

    uv run python -m scripts.smoke_llm                     # manager + voice, thinking off and on
    uv run python -m scripts.smoke_llm --list-models       # Nemotron IDs each provider offers
    uv run python -m scripts.smoke_llm --no-nvidia         # act as if the NVIDIA key were unset
    uv run python -m scripts.smoke_llm --nvidia-down       # NVIDIA unreachable: proves fallback
    uv run python -m scripts.smoke_llm --jobs voice --thinking off --runs 5   # latency spread
"""
import argparse
import asyncio
import statistics
import sys

from app.config import JOBS, get_settings
from app.llm import LLMClient, LLMError

PROMPTS = {
    "manager": (
        "A tornado warning covers 1,500 homes and we have 100 phone lines. "
        "In one sentence, who should we call first and why?"
    ),
    "voice": (
        "You are RollCall, an automated assistant calling about a tornado warning. "
        "In one short sentence, ask the person whether they are safe right now."
    ),
    "resident": (
        "You are Maria, 81, home alone; the power is out and your oxygen machine keeps beeping. "
        "In one sentence, answer: 'Are you safe right now?'"
    ),
}


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


async def run_once(llm: LLMClient, job: str, thinking: bool, provider: str | None) -> float | None | bool:
    """Returns the time to first answer word in ms, None if unmeasured, or False on failure."""
    label = f"{job:<8} thinking {'on ' if thinking else 'off'}"
    try:
        async with llm.stream(
            job,
            [{"role": "user", "content": PROMPTS[job]}],
            thinking=thinking,
            max_tokens=2048 if thinking else 256,
            provider=provider,
            first_chunk_timeout=30,
        ) as stream:
            async for _ in stream:
                pass
    except LLMError as exc:
        print(f"  FAIL  {label}  {exc}")
        return False
    r = stream.result
    print(f"  ok    {label}  {r.provider:<10} {r.model}")
    print(
        f"        first token {_ms(r.ttft_ms)} ms | first answer word {_ms(r.ttft_content_ms)} ms"
        f" | total {_ms(r.latency_ms)} ms | tokens {r.usage.prompt_tokens}+{r.usage.completion_tokens}"
        f" | reasoning {'yes' if r.reasoning else 'no'}"
    )
    answer = " ".join(r.text.split())
    print(f"        answer: {answer[:160] or '(empty; the reply may have hit max_tokens)'}")
    if r.rate_limit:
        print(f"        rate-limit headers: {r.rate_limit}")
    return r.ttft_content_ms


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--jobs", nargs="+", choices=JOBS, default=["manager", "voice"])
    parser.add_argument("--thinking", choices=["off", "on", "both"], default="both")
    parser.add_argument("--runs", type=int, default=1, help="repeat each check to see the spread")
    parser.add_argument("--provider", choices=["nvidia", "openrouter"], help="use only this one")
    parser.add_argument("--no-nvidia", action="store_true", help="act as if NVIDIA keys were unset")
    parser.add_argument("--nvidia-down", action="store_true", help="point NVIDIA at a dead address")
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if args.no_nvidia:
        settings = settings.model_copy(update={"nvidia_api_keys": "", "nvidia_api_key": ""})
    if args.nvidia_down:
        settings = settings.model_copy(update={"nvidia_base_url": "http://127.0.0.1:9/v1"})

    llm = LLMClient(settings)
    try:
        if args.list_models:
            for name in ("nvidia", "openrouter"):
                try:
                    ids = await llm.list_models(name)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    print(f"{name}: could not list models ({exc})")
                    continue
                print(f"{name}: {len(ids)} Nemotron models")
                for model_id in ids:
                    print(f"  {model_id}")
            return 0

        print(f"providers configured: {', '.join(llm.providers) or 'none'}")
        if not llm.providers:
            print("Add NVIDIA_API_KEYS and/or OPENROUTER_API_KEY to .env (see .env.example).")
            return 1
        modes = {"off": [False], "on": [True], "both": [False, True]}[args.thinking]
        failures = 0
        for job in args.jobs:
            for thinking in modes:
                timings = []
                for _ in range(args.runs):
                    outcome = await run_once(llm, job, thinking, args.provider)
                    if outcome is False:
                        failures += 1
                    elif outcome is not None:
                        timings.append(outcome)
                if len(timings) > 1:
                    print(
                        f"        first answer word over {len(timings)} runs:"
                        f" median {statistics.median(timings):.0f} ms, worst {max(timings):.0f} ms"
                    )
        print("\nall checks passed" if not failures else f"\n{failures} check(s) failed")
        return 1 if failures else 0
    finally:
        await llm.aclose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
