"""Create or update the Vapi assistant from code, so its whole config lives in git.

    uv run python -m scripts.vapi_setup --dry-run     # print the request; no API call
    uv run python -m scripts.vapi_setup               # create it, or update VAPI_ASSISTANT_ID
    uv run python -m scripts.vapi_setup --base-url https://new-tunnel.trycloudflare.com

Needs in .env: VAPI_API_KEY and PUBLIC_BASE_URL (the backend's public HTTPS tunnel). Re-run it
whenever the tunnel URL, the voice spec or VAPI_WEBHOOK_SECRET changes.

What it sets up (docs: https://docs.vapi.ai/api-reference/assistants/create):
- model: provider custom-llm. Vapi uses model.url as an OpenAI base URL and appends
  /chat/completions, so model.url is PUBLIC_BASE_URL/vapi and replies come from our backend.
- the voice spec's system prompt, first message and five tools; tool calls go to /vapi/webhook.
- server: /vapi/webhook gets tool-calls, status-update, final transcripts and end-of-call-report.
- a voice and transcriber from .env, a 3-minute cap, and a hang-up when the agent says "goodbye".
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from app.agent import voice_spec
from app.config import Settings, get_settings

VAPI_API = "https://api.vapi.ai"
ASSISTANT_NAME = "RollCall Voice"
SERVER_MESSAGES = ["tool-calls", "status-update", 'transcript[transcriptType="final"]', "end-of-call-report"]
END_CALL_PHRASES = ["goodbye"]  # the spec's closing line ends with "Goodbye."
EXAMPLE_BASE_URL = "https://YOUR-TUNNEL.example.com"


def build_assistant(settings: Settings, base_url: str) -> dict[str, Any]:
    """The assistant's full config, for POST /assistant or PATCH /assistant/{id}."""
    base = base_url.rstrip("/")
    secret_headers = {"X-Vapi-Secret": settings.vapi_webhook_secret} if settings.vapi_webhook_secret else {}

    def server(url: str) -> dict[str, Any]:
        config: dict[str, Any] = {"url": url, "timeoutSeconds": 20}
        if secret_headers:
            config["headers"] = dict(secret_headers)
        return config

    model: dict[str, Any] = {
        "provider": "custom-llm",
        "url": f"{base}/vapi",  # Vapi appends /chat/completions
        "model": settings.voice_model,  # informational; the backend picks the model per provider
        "messages": [{"role": "system", "content": voice_spec.SYSTEM_PROMPT}],
        "tools": [
            {"type": "function", "async": False, "function": tool["function"], "server": server(f"{base}/vapi/webhook")}
            for tool in voice_spec.TOOLS
        ],
        "temperature": voice_spec.TEMPERATURE,
        "maxTokens": voice_spec.MAX_TOKENS,
        "metadataSendMode": "variable",  # requests carry the call and its metadata (household_id)
    }
    if secret_headers:
        model["headers"] = dict(secret_headers)
    return {
        "name": ASSISTANT_NAME,
        "firstMessage": voice_spec.FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "model": model,
        "voice": {"provider": settings.vapi_voice_provider, "voiceId": settings.vapi_voice_id},
        "transcriber": {
            "provider": settings.vapi_transcriber_provider,
            "model": settings.vapi_transcriber_model,
            "language": "en",
        },
        "server": server(f"{base}/vapi/webhook"),
        "serverMessages": list(SERVER_MESSAGES),
        "maxDurationSeconds": settings.vapi_max_call_s,
        "endCallPhrases": list(END_CALL_PHRASES),
        "backgroundSound": "off",
        "metadata": {"app": "rollcall", "voice_spec": f"{voice_spec.VERSION}-{voice_spec.SPEC_HASH}"},
    }


def redacted(value: Any) -> Any:
    """A copy with secret header values hidden, for printing."""
    if isinstance(value, dict):
        return {k: "***" if k.lower() == "x-vapi-secret" else redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    parser.add_argument("--base-url", help="public HTTPS URL of the backend (default: PUBLIC_BASE_URL)")
    args = parser.parse_args()

    settings = get_settings()
    base = (args.base_url or settings.public_base_url).strip().rstrip("/")
    if not base:
        if not args.dry_run:
            print("FAIL  set PUBLIC_BASE_URL in .env (or pass --base-url) to the backend's public HTTPS URL")
            return 1
        base = EXAMPLE_BASE_URL
    if not base.startswith("https://"):
        print(f"WARN  {base} is not HTTPS; Vapi needs a public HTTPS URL (cloudflared or ngrok)")

    payload = build_assistant(settings, base)
    assistant_id = settings.vapi_assistant_id.strip()
    method, url = ("PATCH", f"{VAPI_API}/assistant/{assistant_id}") if assistant_id else ("POST", f"{VAPI_API}/assistant")
    if args.dry_run:
        print(f"# dry run: would {method} {url} (secret headers hidden)", file=sys.stderr)
        print(json.dumps(redacted(payload), indent=2))
        return 0
    if not settings.vapi_api_key:
        print("FAIL  set VAPI_API_KEY in .env (Vapi dashboard > API keys > private key)")
        return 1

    try:
        resp = httpx.request(
            method, url, json=payload, headers={"Authorization": f"Bearer {settings.vapi_api_key}"}, timeout=30
        )
    except httpx.HTTPError as exc:
        print(f"FAIL  could not reach Vapi: {exc}")
        return 1
    if resp.status_code >= 400:
        print(f"FAIL  {method} {url}: HTTP {resp.status_code}\n{resp.text[:2000]}")
        return 1
    new_id = resp.json().get("id", "?")
    print(f"ok    {'updated' if assistant_id else 'created'} assistant {new_id}")
    print(f"      custom LLM: {base}/vapi/chat/completions | webhook: {base}/vapi/webhook")
    if not assistant_id:
        print(f"      put this line in .env so call_me.py uses it and re-runs update it:\n\nVAPI_ASSISTANT_ID={new_id}\n")
    if settings.vapi_phone_number_id:
        # Inbound calls: no fixed assistant on the number, so Vapi asks our webhook (assistant-request),
        # which answers with this assistant filled in for the caller's household.
        phone = {"assistantId": None, "server": payload.get("server")}
        try:
            r = httpx.patch(f"{VAPI_API}/phone-number/{settings.vapi_phone_number_id}", json=phone,
                            headers={"Authorization": f"Bearer {settings.vapi_api_key}"}, timeout=30)
            if r.status_code < 400:
                print("ok    phone number set to ask our webhook who answers inbound calls")
            else:
                print(f"WARN  could not set up inbound calls on the number: HTTP {r.status_code} {r.text[:300]}")
        except httpx.HTTPError as exc:
            print(f"WARN  could not set up inbound calls on the number: {exc}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
