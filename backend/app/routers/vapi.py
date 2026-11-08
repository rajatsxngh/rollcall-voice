"""Phone platform endpoints (M1): the custom-LLM endpoint Vapi calls for every reply, and its webhook.

POST /vapi/chat/completions
    OpenAI-compatible chat completions. Vapi's custom-llm provider uses the assistant's model.url
    as an OpenAI base URL and appends /chat/completions, so scripts/vapi_setup.py sets model.url to
    PUBLIC_BASE_URL/vapi. Each turn runs the voice model, thinking off, and streams OpenAI chunks
    back (SSE `data: {...}` lines, then `data: [DONE]`): the spoken answer with reasoning removed,
    plus tool-call deltas. If the model fails or says nothing, the agent speaks a short fallback line
    instead of going silent. `stream: false` gets one JSON completion.
POST /vapi/webhook
    Vapi's server messages. tool-calls run the voice spec's tools and are answered with
    {"results": [{"name", "toolCallId", "result" or "error"}]}; status-update, transcript and
    end-of-call-report become call.started, call.transcript and call.ended events.

With VAPI_WEBHOOK_SECRET set, both routes require it in the X-Vapi-Secret header (or as a bearer
token); scripts/vapi_setup.py tells Vapi to send it.

Vapi docs: https://docs.vapi.ai/customization/tool-calling-integration (custom LLM),
https://docs.vapi.ai/server-url/events and https://docs.vapi.ai/api-reference/webhooks/server-message
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.agent import voice_spec, voice_tools
from app.db import Database
from app.events import EventType
from app.llm import LLMClient, LLMError, LLMResult, record_usage
from app.models import CallOutcome, Channel

log = logging.getLogger(__name__)

MAX_TOKENS_CAP = 1024
# A caller can't wait the LLM client's default 60 s (Vapi itself gives up after 20 s). Drop a
# provider that hasn't started answering within a few seconds (the client retries once, then
# falls back to OpenRouter), and end a turn whose stream stalls.
FIRST_CHUNK_TIMEOUT_S = 4.0
STREAM_TIMEOUT_S = 10.0


def require_vapi_secret(request: Request) -> None:
    """With VAPI_WEBHOOK_SECRET set, accept only requests that carry it (X-Vapi-Secret or bearer)."""
    secret = request.app.state.settings.vapi_webhook_secret
    if not secret:
        return
    given = request.headers.get("x-vapi-secret", "")
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if _matches(given, secret) or _matches(bearer, secret):
        return
    log.warning("rejected %s: missing or wrong X-Vapi-Secret header", request.url.path)
    raise HTTPException(status_code=401, detail="missing or wrong X-Vapi-Secret header")


def _matches(given: str, secret: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), secret.encode())


router = APIRouter(prefix="/vapi", tags=["vapi"], dependencies=[Depends(require_vapi_secret)])


# --- Custom LLM: one model turn per request ------------------------------------------------


@dataclass
class _Turn:
    """One model turn on a call, for the reply-delay log line and the llm_usage table."""

    started: float  # when the request arrived (perf_counter)
    household_id: int | None = None
    call_id: str | None = None
    result: LLMResult | None = None
    first_answer_ms: float | None = None  # request in -> first spoken word or tool call out
    tools_called: list[str] = field(default_factory=list)
    problem: str | None = None  # what went wrong with the model, if anything
    fallback_spoken: bool = False

    def mark_answer(self) -> None:
        if self.first_answer_ms is None:
            self.first_answer_ms = (time.perf_counter() - self.started) * 1000


@dataclass
class _Reply:
    """Formats one assistant turn the way OpenAI does."""

    model: str
    id: str = field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    created: int = field(default_factory=lambda: int(time.time()))

    def chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> str:
        data = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.post("/chat/completions")
# Also answer if model.url was set to the full path and Vapi appended /chat/completions again.
@router.post("/chat/completions/chat/completions", include_in_schema=False)
async def chat_completions(request: Request) -> Any:
    started = time.perf_counter()
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    state = request.app.state
    # Vapi also sends model, call, metadata, customer, phoneNumber and more; only these matter here.
    messages = _clean_messages(body.get("messages"), _caller_name(body))
    options: dict[str, Any] = {
        "tools": _clean_tools(body.get("tools")) or voice_spec.TOOLS,
        "thinking": False,
        "max_tokens": _max_tokens(body),
        "temperature": _temperature(body),
    }
    if body.get("tool_choice") is not None:
        options["tool_choice"] = body["tool_choice"]
    turn = _Turn(started=started, household_id=resolve_household(state.db, body), call_id=_call_id(body))
    reply = _Reply(model=state.settings.voice_model)
    finish = BackgroundTask(_finish_turn, state.db, turn)
    if body.get("stream") is False:
        return JSONResponse(await _complete(state.llm, messages, options, turn, reply), background=finish)
    return StreamingResponse(
        _stream(state.llm, messages, options, turn, reply),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=finish,
    )


async def _stream(
    llm: LLMClient, messages: list[dict[str, Any]], options: dict[str, Any], turn: _Turn, reply: _Reply
) -> AsyncIterator[str]:
    yield reply.chunk({"role": "assistant", "content": ""})
    tool_calls = _ToolCallRelay(turn)
    spoke = False
    stream = None
    try:
        async with llm.stream(
            "voice", messages, **options, timeout=STREAM_TIMEOUT_S, first_chunk_timeout=FIRST_CHUNK_TIMEOUT_S
        ) as stream:
            async for delta in stream:
                out: dict[str, Any] = {}
                if delta.text:  # answer text only: <think> blocks and reasoning fields are removed
                    out["content"] = delta.text
                calls = tool_calls.relay(delta.chunk)
                if calls:
                    out["tool_calls"] = calls
                if not out:
                    continue
                if delta.text.strip() or calls:
                    turn.mark_answer()
                    spoke = spoke or bool(delta.text.strip())
                yield reply.chunk(out)
    except LLMError as exc:
        turn.problem = f"model unavailable: {exc}"
    except Exception as exc:  # the stream broke after it started: end the turn cleanly
        log.exception("voice model stream broke mid-reply")
        turn.problem = f"stream broke: {type(exc).__name__}"
    finally:
        if stream is not None:
            turn.result = stream.result
    if not spoke and not tool_calls.ids:
        turn.problem = turn.problem or "empty reply"
        turn.fallback_spoken = True
        yield reply.chunk({"content": voice_spec.FALLBACK_REPLY})
    yield reply.chunk({}, "tool_calls" if tool_calls.ids else "stop")
    yield "data: [DONE]\n\n"


async def _complete(
    llm: LLMClient, messages: list[dict[str, Any]], options: dict[str, Any], turn: _Turn, reply: _Reply
) -> dict[str, Any]:
    result: LLMResult | None = None
    try:
        result = await llm.chat("voice", messages, **options, timeout=STREAM_TIMEOUT_S)
    except LLMError as exc:
        turn.problem = f"model unavailable: {exc}"
    turn.result = result
    text = result.text if result else ""
    calls = [_clean_tool_call(c) for c in (result.tool_calls if result else []) if isinstance(c, dict)]
    turn.tools_called = [c["function"]["name"] for c in calls]
    if text.strip() or calls:
        turn.mark_answer()
    else:
        turn.problem = turn.problem or "empty reply"
        turn.fallback_spoken = True
        text = voice_spec.FALLBACK_REPLY
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = calls
    prompt_tokens = result.usage.prompt_tokens if result else 0
    completion_tokens = result.usage.completion_tokens if result else 0
    return {
        "id": reply.id,
        "object": "chat.completion",
        "created": reply.created,
        "model": reply.model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class _ToolCallRelay:
    """Passes the provider's tool-call deltas on as clean OpenAI deltas. The id, type and name go
    out once per call (an id is made up if the provider sent none); arguments go out as they stream."""

    def __init__(self, turn: _Turn) -> None:
        self.turn = turn
        self.ids: dict[int, str] = {}
        self._named: set[int] = set()

    def relay(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            for call in (choice.get("delta") or {}).get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                index = call["index"] if isinstance(call.get("index"), int) else 0
                function = _dict(call.get("function"))
                item: dict[str, Any] = {"index": index}
                piece: dict[str, Any] = {}
                if index not in self.ids:
                    self.ids[index] = str(call.get("id") or f"call_{uuid.uuid4().hex[:24]}")
                    item.update(id=self.ids[index], type="function")
                if function.get("name") and index not in self._named:
                    self._named.add(index)
                    piece["name"] = str(function["name"])
                    self.turn.tools_called.append(piece["name"])
                arguments = function.get("arguments")
                if arguments:
                    piece["arguments"] = arguments if isinstance(arguments, str) else json.dumps(arguments)
                elif "id" in item:
                    piece["arguments"] = ""
                if piece:
                    item["function"] = piece
                if len(item) > 1:
                    out.append(item)
        return out


def _finish_turn(db: Database, turn: _Turn) -> None:
    """After the reply is sent: log the reply delay and save it, with the tokens, to llm_usage."""
    result = turn.result
    log.info(
        "voice turn (call %s): first answer %s ms | total %.0f ms | %s | tools: %s%s%s",
        turn.call_id or "-",
        "n/a" if turn.first_answer_ms is None else f"{turn.first_answer_ms:.0f}",
        (time.perf_counter() - turn.started) * 1000,
        f"{result.provider} {result.model}, {result.usage.prompt_tokens}+{result.usage.completion_tokens} tokens"
        if result
        else "no model reply",
        ", ".join(turn.tools_called) or "none",
        " | cut off by max_tokens" if result and result.finish_reason == "length" else "",
        (f" | {turn.problem}" if turn.problem else "") + (" | spoke the fallback line" if turn.fallback_spoken else ""),
    )
    if result is None:
        return
    try:
        # ttft_ms here is the delay the caller hears: request in -> first answer token out.
        record_usage(db, replace(result, ttft_ms=turn.first_answer_ms), turn.household_id)
    except Exception:
        log.exception("could not save the voice turn's usage")


_MESSAGE_KEYS = ("role", "content", "name", "tool_calls", "tool_call_id")


def _clean_messages(raw: Any, caller_name: str | None) -> list[dict[str, Any]]:
    """The request's messages, trimmed to the OpenAI fields every provider accepts. Adds the spec's
    system prompt when the request has none, so the endpoint can be tried by hand."""
    messages: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("role"), str):
            continue
        message = {key: item[key] for key in _MESSAGE_KEYS if item.get(key) is not None}
        calls = message.pop("tool_calls", None)
        if isinstance(calls, list):
            calls = [_clean_tool_call(c) for c in calls if isinstance(c, dict)]
            if calls:
                message["tool_calls"] = calls
        message.setdefault("content", None if "tool_calls" in message else "")
        messages.append(message)
    if not messages or messages[0]["role"] != "system":
        messages.insert(0, voice_spec.start_messages(caller_name or "the resident")[0])
    return messages


def _clean_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    function = _dict(call.get("function"))
    arguments = function.get("arguments", "")
    return {
        "id": str(call.get("id") or f"call_{uuid.uuid4().hex[:24]}"),
        "type": "function",
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


def _clean_tools(raw: Any) -> list[dict[str, Any]] | None:
    """Tools in the request (Vapi sends the assistant's), as plain OpenAI function tools."""
    tools = []
    for tool in raw if isinstance(raw, list) else []:
        function = _dict(tool.get("function")) if isinstance(tool, dict) else {}
        if not function.get("name"):
            continue
        clean: dict[str, Any] = {"name": function["name"]}
        if function.get("description"):
            clean["description"] = function["description"]
        if isinstance(function.get("parameters"), dict):
            clean["parameters"] = function["parameters"]
        tools.append({"type": "function", "function": clean})
    return tools or None


def _max_tokens(body: dict[str, Any]) -> int:
    for key in ("max_tokens", "max_completion_tokens"):
        value = body.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return min(int(value), MAX_TOKENS_CAP)
    return voice_spec.MAX_TOKENS


def _temperature(body: dict[str, Any]) -> float:
    value = body.get("temperature")
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 2:
        return float(value)
    return voice_spec.TEMPERATURE


# --- Call details carried on requests and server messages ----------------------------------


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def household_id_from(container: dict[str, Any]) -> int | None:
    """The household a call is about, from the metadata the call was placed with.

    Works on a webhook message and on a custom-LLM request body (Vapi sends the assistant's
    metadata as `metadata` and the call as `call`). Absent on M1 test calls."""
    call = _dict(container.get("call"))
    overrides = _dict(call.get("assistantOverrides"))
    for value in (
        _dict(overrides.get("metadata")).get("household_id"),
        _dict(container.get("metadata")).get("household_id"),
        _dict(_dict(container.get("assistant")).get("metadata")).get("household_id"),
        _dict(call.get("metadata")).get("household_id"),
        _dict(overrides.get("variableValues")).get("household_id"),
    ):
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def caller_number(container: dict[str, Any]) -> str | None:
    """The other party's phone number (the caller on inbound calls, the callee on outbound)."""
    call = _dict(container.get("call"))
    for value in (_dict(call.get("customer")).get("number"), _dict(container.get("customer")).get("number")):
        if value:
            return str(value)
    return None


def resolve_household(db: Database, container: dict[str, Any]) -> int | None:
    """Metadata first (outbound calls we place); otherwise match the phone number to a household.
    Inbound calls (someone dials the RollCall number) only have the number. Live demo homes win."""
    found = household_id_from(container)
    if found is not None:
        return found
    number = caller_number(container)
    digits = "".join(ch for ch in number or "" if ch.isdigit())[-10:]
    if len(digits) < 10:
        return None
    from sqlmodel import select

    from app.models import Household

    with db.session() as session:
        matches = [h for h in session.exec(select(Household)).all()
                   if "".join(ch for ch in h.phone if ch.isdigit())[-10:] == digits]
    matches.sort(key=lambda h: (not h.live, h.id))
    return matches[0].id if matches else None


def _call_id(container: dict[str, Any]) -> str | None:
    value = _dict(container.get("call")).get("id")
    return str(value) if value else None


def _caller_name(container: dict[str, Any]) -> str | None:
    call = _dict(container.get("call"))
    name = (
        _dict(_dict(call.get("assistantOverrides")).get("variableValues")).get("name")
        or _dict(container.get("customer")).get("name")
        or _dict(call.get("customer")).get("name")
    )
    return str(name) if name else None


def _attempt(message: dict[str, Any]) -> int:
    overrides = _dict(_dict(message.get("call")).get("assistantOverrides"))
    for metadata in (_dict(overrides.get("metadata")), _dict(message.get("metadata"))):
        value = metadata.get("attempt")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 1


# --- Webhook: Vapi's server messages ----------------------------------------------------------


class _CallTracker:
    """What the webhook remembers about live calls, per app: calls already announced, end_call
    summaries waiting for the end-of-call report, and tool results by toolCallId (a repeated
    delivery gets the same answer instead of running the tool twice). Bounded; lost on restart."""

    LIMIT = 1000

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._started: OrderedDict[str, None] = OrderedDict()
        self._summaries: OrderedDict[str, str] = OrderedDict()
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def first_start(self, call_id: str) -> bool:
        with self.lock:
            if call_id in self._started:
                return False
            self._keep(self._started, call_id, None)
            return True

    def save_summary(self, call_id: str, summary: str) -> None:
        with self.lock:
            self._keep(self._summaries, call_id, summary)

    def take_summary(self, call_id: str) -> str | None:
        with self.lock:
            return self._summaries.pop(call_id, None)

    def result_for(self, tool_call_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._results.get(tool_call_id)

    def save_result(self, tool_call_id: str, entry: dict[str, Any]) -> None:
        with self.lock:
            self._keep(self._results, tool_call_id, entry)

    def _keep(self, store: OrderedDict, key: str, value: Any) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self.LIMIT:
            store.popitem(last=False)


_TRACKER_LOCK = threading.Lock()


def _call_tracker(state: Any) -> _CallTracker:
    tracker = getattr(state, "vapi_calls", None)
    if tracker is None:
        with _TRACKER_LOCK:
            tracker = getattr(state, "vapi_calls", None)
            if tracker is None:
                tracker = state.vapi_calls = _CallTracker()
    return tracker


def assistant_for_inbound(state: Any, message: dict[str, Any]) -> dict[str, Any]:
    """Someone dialed the RollCall number (no assistant on the number): answer with our assistant,
    filled in for the household that number belongs to. Vapi allows 7.5 s, so this stays a quick lookup."""
    assistant_id = state.settings.vapi_assistant_id.strip()
    if not assistant_id:
        return {"error": "RollCall isn't set up to take calls right now. Please try again later."}
    household_id = resolve_household(state.db, message)
    household = voice_tools.find_household(state.db, household_id) if household_id is not None else None
    name = household.name.split()[0] if household is not None else "there"
    overrides: dict[str, Any] = {"variableValues": {"name": name}}
    if household is not None:
        overrides["metadata"] = {"household_id": household.id}
    log.info("inbound call from household %s", household_id)
    return {"assistantId": assistant_id, "assistantOverrides": overrides}


@router.post("/webhook")
def vapi_webhook(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    message = body.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail='expected a Vapi server message: {"message": {...}}')
    kind = str(message.get("type") or "")
    state = request.app.state
    calls = _call_tracker(state)
    if kind == "tool-calls":
        return _answer_tool_calls(state, calls, message)
    if kind == "assistant-request":
        return assistant_for_inbound(state, message)
    if kind == "status-update":
        _status_update(state, calls, message)
    elif kind.startswith("transcript"):  # "transcript" or 'transcript[transcriptType="final"]'
        _transcript(state, message)
    elif kind == "end-of-call-report":
        _end_of_call(state, calls, message)
    else:
        log.debug("ignoring Vapi %s message", kind or "untyped")
    return {"ok": True}


def _answer_tool_calls(state: Any, calls: _CallTracker, message: dict[str, Any]) -> dict[str, Any]:
    ctx = voice_tools.ToolContext(
        db=state.db,
        bus=state.bus,
        household_id=resolve_household(state.db, message),
        call_id=_call_id(message),
        caller_name=_caller_name(message),
    )
    results = []
    with calls.lock:  # one at a time, so a repeated delivery can't run a tool twice
        for tool_call in _tool_call_list(message):
            tool_call_id = str(tool_call.get("id") or "")
            function = _dict(tool_call.get("function"))
            name = str(function.get("name") or tool_call.get("name") or "")
            entry = calls.result_for(tool_call_id) if tool_call_id else None
            if entry is None:
                entry = _run_tool(ctx, calls, tool_call_id, name, function.get("arguments"))
                if tool_call_id:
                    calls.save_result(tool_call_id, entry)
            results.append(entry)
    return {"results": results}


def _tool_call_list(message: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = message.get("toolCallList")
    if isinstance(tool_calls, list) and tool_calls:
        return [c for c in tool_calls if isinstance(c, dict)]
    return [
        item["toolCall"]
        for item in message.get("toolWithToolCallList") or []
        if isinstance(item, dict) and isinstance(item.get("toolCall"), dict)
    ]


def _run_tool(
    ctx: voice_tools.ToolContext, calls: _CallTracker, tool_call_id: str, name: str, arguments: Any
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "toolCallId": tool_call_id}
    try:
        entry["result"] = voice_tools.run_tool(name, arguments, ctx)
    except voice_tools.UnknownTool:
        log.warning("Vapi asked for an unknown tool %r", name)
        entry["error"] = f"There is no tool called {name!r}."
    except Exception:
        log.exception("tool %s failed", name)
        entry["error"] = f"The {name} tool failed. Carry on without it."
    else:
        if name == "end_call" and ctx.call_id:
            summary = voice_tools.parse_arguments(arguments).get("summary")
            if summary:
                calls.save_summary(ctx.call_id, str(summary))
    log.info("tool %s (call %s): %s", name, ctx.call_id or "-", entry.get("result") or entry.get("error"))
    return entry


def _known_household(state: Any, message: dict[str, Any]) -> int | None:
    """The call's household id, only if that household exists (events never point at nothing)."""
    household = voice_tools.find_household(state.db, resolve_household(state.db, message))
    return household.id if household else None


def _status_update(state: Any, calls: _CallTracker, message: dict[str, Any]) -> None:
    status = str(message.get("status") or "")
    call_id = _call_id(message)
    if status in ("queued", "ringing", "in-progress") and calls.first_start(call_id or ""):
        state.bus.emit(
            EventType.CALL_STARTED,
            {"call_id": call_id, "channel": Channel.VAPI, "attempt": _attempt(message), "status": status},
            household_id=_known_household(state, message),
        )
        return
    reason = message.get("endedReason")
    log.info("Vapi call %s: %s%s", call_id or "-", status or "unknown status", f" ({reason})" if reason else "")


def _transcript(state: Any, message: dict[str, Any]) -> None:
    if message.get("transcriptType", "final") != "final":
        return  # partial transcripts are skipped; the final line follows
    line = " ".join(str(message.get("transcript") or "").split())
    if not line:
        return
    state.bus.emit(
        EventType.CALL_TRANSCRIPT,
        {
            "call_id": _call_id(message),
            "role": "agent" if message.get("role") == "assistant" else "resident",
            "line": line,  # not `text`: transcript lines stay out of the agent feed
        },
        household_id=_known_household(state, message),
    )


def _end_of_call(state: Any, calls: _CallTracker, message: dict[str, Any]) -> None:
    call_id = _call_id(message)
    reason = str(message.get("endedReason") or _dict(message.get("call")).get("endedReason") or "")
    agent_summary = calls.take_summary(call_id) if call_id else None
    payload: dict[str, Any] = {
        "call_id": call_id,
        "outcome": _outcome(reason, agent_finished=agent_summary is not None),
        "summary": agent_summary or str(_dict(message.get("analysis")).get("summary") or ""),
        "ended_reason": reason,
    }
    seconds = _duration_s(message)
    if seconds is not None:
        payload["duration_s"] = seconds
    state.bus.emit(EventType.CALL_ENDED, payload, household_id=_known_household(state, message))


_NO_ANSWER = frozenset({"customer-did-not-answer", "customer-busy", "voicemail", "vonage-rejected"})
_FAILED_WORDS = (
    "error", "fault", "failed", "not-found", "not-valid", "misdialed", "canceled",
    "closed-websocket", "shutdown", "join-timed-out", "microphone",
)


def _outcome(reason: str, agent_finished: bool) -> CallOutcome:
    """Vapi's endedReason as one of our call outcomes."""
    if reason in _NO_ANSWER:
        return CallOutcome.NO_ANSWER
    if "forward" in reason or "transfer" in reason:
        return CallOutcome.TRANSFERRED
    if any(word in reason for word in _FAILED_WORDS):
        return CallOutcome.FAILED
    if reason == "customer-ended-call" and not agent_finished:
        return CallOutcome.HUNG_UP
    return CallOutcome.COMPLETED


def _duration_s(message: dict[str, Any]) -> float | None:
    seconds = message.get("durationSeconds")
    if isinstance(seconds, int | float) and not isinstance(seconds, bool):
        return round(float(seconds), 1)
    call = _dict(message.get("call"))
    try:
        started = datetime.fromisoformat(message.get("startedAt") or call.get("startedAt"))
        ended = datetime.fromisoformat(message.get("endedAt") or call.get("endedAt"))
        return round((ended - started).total_seconds(), 1)
    except (TypeError, ValueError):
        return None
