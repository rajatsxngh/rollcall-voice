"""Preflight: run before every demo. Checks keys, models, the tunnel, Vapi, Twilio, data and ports.

    uv run python -m scripts.preflight                 # backend expected on 127.0.0.1:8100
    uv run python -m scripts.preflight --call +12145550123   # also place a test call to that phone

Prints PASS, WARN or FAIL per check; exits 1 if anything failed.
"""
import argparse
import asyncio
import socket
import sqlite3
import subprocess
import sys

import httpx

from app.config import get_settings
from app.llm import LLMClient

RESULTS: list[tuple[str, str, str]] = []


def check(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"{status:<5} {name}{': ' + detail if detail else ''}")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--call", help="place a test call to this consenting phone")
    args = parser.parse_args()
    s = get_settings()

    # Data
    base_db, live_db = s.data_dir / "base.db", s.live_db_path
    if base_db.exists() and live_db.exists():
        with sqlite3.connect(live_db) as c:
            homes = c.execute("select count(*), sum(live) from households").fetchone()
        check("PASS" if homes[0] else "FAIL", "City seeded", f"{homes[0]:,} households, {homes[1] or 0} live demo")
        check("PASS" if homes[1] else "WARN", "Demo caller", "set" if homes[1] else "none: uv run python -m scripts.demo_caller --name ... --phone ...")
    else:
        check("FAIL", "City seeded", "run: uv run python -m scripts.seed")
    results = s.data_dir.parent / "results" / "latest.json"
    check("PASS" if results.exists() else "WARN", "Results for the results view", str(results) if results.exists() else "run: uv run python -m scripts.run_eval")
    recordings = list((s.data_dir / "recordings").glob("*.jsonl")) if (s.data_dir / "recordings").exists() else []
    check("PASS" if recordings else "WARN", "Backup recording", f"{len(recordings)} saved" if recordings
          else "after a good run: curl -X POST \"http://127.0.0.1:8100/recordings/save?name=good\"")

    # Models
    llm = LLMClient(s)
    try:
        if not llm.providers:
            check("FAIL", "Model keys", "set NVIDIA_API_KEYS (and OPENROUTER_API_KEY) in .env")
        else:
            check("PASS", "Model keys", f"{', '.join(llm.providers)}; {len(s.nvidia_keys)} NVIDIA key(s)")
            for job in ("voice", "manager"):
                try:
                    async with llm.stream(job, [{"role": "user", "content": "Say OK."}], max_tokens=8,
                                          first_chunk_timeout=10) as stream:
                        async for _ in stream:
                            pass
                    r = stream.result
                    ms = r.ttft_content_ms or r.ttft_ms or r.latency_ms
                    status = "PASS" if job != "voice" or ms < 1000 else "WARN"
                    check(status, f"{job} model answers", f"{r.provider} {r.model}, first word {ms:.0f} ms")
                except Exception as exc:  # noqa: BLE001
                    check("FAIL", f"{job} model answers", str(exc)[:160])
    finally:
        await llm.aclose()

    # Backend and tunnel
    async with httpx.AsyncClient(timeout=8) as http:
        try:
            health = (await http.get(f"{args.base}/health")).json()
            check("PASS", "Backend", f"{args.base}, last event {health['last_event_id']}")
        except Exception as exc:  # noqa: BLE001
            check("FAIL", "Backend", f"not reachable at {args.base} ({type(exc).__name__}); start it: uv run uvicorn app.main:app --port 8100")
        if s.public_base_url:
            try:
                r = await http.get(f"{s.public_base_url.rstrip('/')}/health")
                check("PASS" if r.status_code == 200 else "FAIL", "Tunnel", f"{s.public_base_url} -> HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                check("FAIL", "Tunnel", f"{s.public_base_url} unreachable ({type(exc).__name__}); restart cloudflared and update PUBLIC_BASE_URL")
        else:
            check("FAIL", "Tunnel", "PUBLIC_BASE_URL is empty: cloudflared tunnel --url http://localhost:8100")

        # Vapi
        if s.vapi_api_key and s.vapi_assistant_id:
            try:
                r = await http.get(f"https://api.vapi.ai/assistant/{s.vapi_assistant_id}",
                                   headers={"Authorization": f"Bearer {s.vapi_api_key}"})
                url = (r.json().get("model") or {}).get("url", "") if r.status_code == 200 else ""
                ok = bool(s.public_base_url) and url.startswith(s.public_base_url.rstrip("/"))
                check("PASS" if ok else "FAIL", "Vapi assistant points at the tunnel",
                      url or f"HTTP {r.status_code}" if not ok else url)
                if not ok:
                    print("      fix: uv run python -m scripts.vapi_setup")
            except Exception as exc:  # noqa: BLE001
                check("FAIL", "Vapi assistant", str(exc)[:160])
        else:
            check("FAIL", "Vapi", "set VAPI_API_KEY, then run scripts.vapi_setup to get VAPI_ASSISTANT_ID")
        check("PASS" if s.vapi_phone_number_id else "FAIL", "Vapi phone number",
              "set" if s.vapi_phone_number_id else "import a Twilio number into Vapi; put its id in VAPI_PHONE_NUMBER_ID")
    check("PASS" if s.dispatcher_phone else "WARN", "Dispatcher phone", s.dispatcher_phone or "set DISPATCHER_PHONE for P1 transfers")
    twilio = all([s.twilio_account_sid, s.twilio_auth_token, s.twilio_from_number])
    check("PASS" if twilio else "WARN", "Twilio texting", "set" if twilio else "set TWILIO_* for real texts")
    check("PASS" if not s.debug_endpoints else "WARN", "Debug endpoints off", "" if not s.debug_endpoints else "set DEBUG_ENDPOINTS=false before judging")
    check("PASS" if port_open(5173) else "WARN", "Dashboard dev server", "http://127.0.0.1:5173" if port_open(5173) else "cd dashboard; npm run dev")

    if args.call:
        print(f"Placing a test call to {args.call} (only call people who agreed)...")
        code = subprocess.call([sys.executable, "-m", "scripts.call_me", args.call, "Preflight"])
        check("PASS" if code == 0 else "FAIL", "Test call placed", args.call)

    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed or warned; {len(failed)} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
