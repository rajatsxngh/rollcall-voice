"""Event stream tools. The backend must be running (uv run uvicorn app.main:app).

    uv run python -m scripts.events_cli check              # emit -> WebSocket -> GET /events
    uv run python -m scripts.events_cli watch --since 0    # print events as they arrive
    uv run python -m scripts.events_cli emit agent.note '{"text": "hello"}'
"""
import argparse
import asyncio
import json
import sys
import time
import uuid

import httpx
from websockets.asyncio.client import connect as ws_connect


def ws_url(base: str) -> str:
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"


async def check(base: str) -> int:
    """The M0 done-when check: an emitted event reaches a WebSocket client within a second and is
    in the history."""
    marker = uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(base_url=base, timeout=10) as http, ws_connect(ws_url(base)) as ws:
        started = time.perf_counter()
        resp = await http.post("/debug/emit", json={"type": "debug.ping", "payload": {"marker": marker}})
        resp.raise_for_status()
        sent = resp.json()
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if event.get("payload", {}).get("marker") == marker:
                    break
        except TimeoutError:
            print(f"FAIL  event #{sent['id']} never reached the WebSocket")
            return 1
        elapsed = (time.perf_counter() - started) * 1000
        history = (await http.get("/events", params={"since": sent["id"] - 1, "limit": 5})).json()
    in_history = any(e["id"] == sent["id"] for e in history["events"])
    ok = elapsed < 1000 and in_history
    print(
        f"{'PASS' if ok else 'FAIL'}  event #{sent['id']}: reached the WebSocket in {elapsed:.0f} ms"
        f" (limit 1000); in GET /events: {'yes' if in_history else 'no'}"
    )
    return 0 if ok else 1


async def watch(base: str, since: int | None) -> int:
    url = ws_url(base) + (f"?since={since}" if since is not None else "")
    print(f"watching {url} (Ctrl+C to stop)")
    async with ws_connect(url) as ws:
        async for raw in ws:
            event = json.loads(raw)
            who = f" household={event['household_id']}" if event.get("household_id") is not None else ""
            print(f"#{event['id']} {event['time']} {event['type']}{who} {json.dumps(event['payload'])}")
    return 0


def emit(base: str, event_type: str, payload: str, household_id: int | None) -> int:
    body = {"type": event_type, "payload": json.loads(payload), "household_id": household_id}
    resp = httpx.post(f"{base}/debug/emit", json=body, timeout=10)
    if resp.status_code != 200:
        print(f"FAIL  HTTP {resp.status_code}: {resp.text}")
        return 1
    print(json.dumps(resp.json()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="backend URL")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="round-trip test (M0 done-when)")
    watch_cmd = sub.add_parser("watch", help="print events as they arrive")
    watch_cmd.add_argument("--since", type=int, help="replay events after this id first")
    emit_cmd = sub.add_parser("emit", help="send one event")
    emit_cmd.add_argument("type")
    emit_cmd.add_argument("payload", nargs="?", default="{}", help="JSON object")
    emit_cmd.add_argument("--household-id", type=int)
    args = parser.parse_args()
    base = args.base.rstrip("/")
    try:
        if args.command == "check":
            return asyncio.run(check(base))
        if args.command == "watch":
            return asyncio.run(watch(base, args.since))
        return emit(base, args.type, args.payload, args.household_id)
    except (httpx.ConnectError, ConnectionRefusedError, OSError) as exc:
        print(f"FAIL  can't reach {base} ({exc}). Is the backend running?")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
