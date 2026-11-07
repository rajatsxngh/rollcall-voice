"""Place one outbound check-in call through Vapi.

    uv run python -m scripts.call_me +12145551234 Maria --dry-run
    uv run python -m scripts.call_me +12145551234 Maria
    uv run python -m scripts.call_me +12145551234 Maria --household-id 12

Only call people who agreed to be called (teammates, the judge's handset).

Needs in .env: VAPI_API_KEY, VAPI_ASSISTANT_ID (from scripts.vapi_setup) and VAPI_PHONE_NUMBER_ID.
Vapi can't place outbound calls from its free numbers: import a Twilio, Vonage or Telnyx number
in the Vapi dashboard and use that number's id (https://docs.vapi.ai/calls/outbound-calling).
The name fills the {{name}} variable in the opening line; --household-id goes in the call's
metadata so needs recorded during the call are saved to that household.
Watch the call's events with: uv run python -m scripts.events_cli watch
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

import httpx

from app.config import Settings, get_settings

VAPI_API = "https://api.vapi.ai"
CONSENT_REMINDER = "Reminder: only call people who agreed to be called (teammates, the judge's handset)."


def normalize_phone(raw: str) -> str:
    """E.164 (+12145551234). Plain 10-digit US numbers get +1."""
    digits = re.sub(r"\D", "", raw)
    if not raw.strip().startswith("+"):
        if len(digits) == 10:
            digits = "1" + digits
    number = "+" + digits
    if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
        raise ValueError(f"{raw!r} is not a phone number; use E.164, like +12145551234")
    return number


def build_call(settings: Settings, phone: str, name: str, household_id: int | None = None) -> dict[str, Any]:
    """The body for POST /call (https://docs.vapi.ai/api-reference/calls/create)."""
    metadata: dict[str, Any] = {"source": "call_me"}
    if household_id is not None:
        metadata["household_id"] = household_id
    return {
        "assistantId": settings.vapi_assistant_id,
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {"number": phone, "name": name},
        "assistantOverrides": {"variableValues": {"name": name}, "metadata": metadata},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phone", help="number to call, E.164 (+12145551234)")
    parser.add_argument("name", help="who we expect to answer; used in the opening line")
    parser.add_argument("--household-id", type=int, help="household this call is about (stored in metadata)")
    parser.add_argument("--dry-run", action="store_true", help="print the request instead of placing the call")
    args = parser.parse_args()

    print(CONSENT_REMINDER)
    try:
        phone = normalize_phone(args.phone)
    except ValueError as exc:
        print(f"FAIL  {exc}")
        return 1
    settings = get_settings()
    body = build_call(settings, phone, args.name.strip(), args.household_id)
    if args.dry_run:
        print(f"# dry run: would POST {VAPI_API}/call", file=sys.stderr)
        print(json.dumps(body, indent=2))
        return 0
    missing = [
        env
        for env, value in (
            ("VAPI_API_KEY", settings.vapi_api_key),
            ("VAPI_ASSISTANT_ID", settings.vapi_assistant_id),
            ("VAPI_PHONE_NUMBER_ID", settings.vapi_phone_number_id),
        )
        if not value
    ]
    if missing:
        print(f"FAIL  set {', '.join(missing)} in .env (VAPI_ASSISTANT_ID comes from scripts.vapi_setup)")
        return 1

    try:
        resp = httpx.post(
            f"{VAPI_API}/call", json=body, headers={"Authorization": f"Bearer {settings.vapi_api_key}"}, timeout=30
        )
    except httpx.HTTPError as exc:
        print(f"FAIL  could not reach Vapi: {exc}")
        return 1
    if resp.status_code >= 400:
        print(f"FAIL  POST {VAPI_API}/call: HTTP {resp.status_code}\n{resp.text[:2000]}")
        return 1
    call = resp.json()
    print(f"ok    calling {phone} ({args.name}): call {call.get('id', '?')}, status {call.get('status', '?')}")
    print("      watch it: uv run python -m scripts.events_cli watch")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
