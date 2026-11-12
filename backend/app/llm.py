"""One Nemotron client for every job (manager, voice, resident).

- build.nvidia.com first, OpenRouter as fallback; both speak the OpenAI chat API.
- Thinking on or off per call. How each provider is switched is set in .env.
- One retry per provider on timeouts, rate limits and server errors, rotating keys.
- A concurrency cap per provider, an opt-in disk cache (for simulations and tests),
  and token usage plus timing on every result.

    llm = LLMClient(get_settings())
    result = await llm.chat("manager", messages, thinking=True)

    async with llm.stream("voice", messages) as stream:
        async for delta in stream:
            print(delta.text, end="")      # answer text only, reasoning removed
    print(stream.result.ttft_content_ms)   # time to the first word of the answer
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.config import PROVIDERS, Settings, get_settings

if TYPE_CHECKING:
    from app.db import Database

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RETRY_DELAY_S = 0.5
OPENROUTER_HEADERS = {"X-Title": "RollCall Voice"}


class LLMError(RuntimeError):
    """No provider produced an answer."""


class _ProviderFailed(Exception):
    pass


class _StreamError(Exception):
    pass


# --- Results --------------------------------------------------------------------------


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResult:
    text: str  # the answer, with any reasoning removed
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    job: str = ""
    provider: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    ttft_ms: float | None = None  # streams only: first token of any kind
    ttft_content_ms: float | None = None  # streams only: first token of the answer itself
    cached: bool = False
    rate_limit: dict[str, str] = field(default_factory=dict)

    def to_cache(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_cache(cls, raw: str) -> LLMResult:
        data = json.loads(raw)
        data["usage"] = Usage(**(data.get("usage") or {}))
        return cls(**data)


@dataclass
class StreamDelta:
    chunk: dict[str, Any]  # the provider's raw OpenAI-format chunk ({} for the final flush)
    text: str = ""  # new answer text in this chunk, reasoning removed
    reasoning: str = ""  # new reasoning text in this chunk


# --- Reasoning in <think> tags ----------------------------------------------------------

_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.S)


def split_think(text: str) -> tuple[str, str]:
    """Split a full reply into (answer, reasoning). Handles <think> blocks, a stray closing tag
    (the prompt template opened the block) and an unclosed block (reply cut off mid-thought)."""
    reasoning = [m.strip() for m in _THINK_BLOCK.findall(text)]
    answer = _THINK_BLOCK.sub("", text)
    if "</think>" in answer:
        before, _, answer = answer.rpartition("</think>")
        reasoning.insert(0, before.strip())
    if "<think>" in answer:
        answer, _, tail = answer.partition("<think>")
        reasoning.append(tail.strip())
    return answer.strip(), "\n".join(r for r in reasoning if r)


class ThinkSplitter:
    """Separates <think>...</think> from answer text as it streams in, even when a tag is split
    across chunks, so a voice agent never speaks its reasoning."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._thinking = False

    def feed(self, text: str) -> tuple[str, str]:
        self._buffer += text
        answer: list[str] = []
        reasoning: list[str] = []
        while self._buffer:
            tag = self.CLOSE if self._thinking else self.OPEN
            out = reasoning if self._thinking else answer
            index = self._buffer.find(tag)
            if index >= 0:
                out.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._thinking = not self._thinking
                continue
            keep = _partial_tag_suffix(self._buffer, tag)
            out.append(self._buffer[: len(self._buffer) - keep])
            self._buffer = self._buffer[len(self._buffer) - keep :]
            break
        return "".join(answer), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        rest, self._buffer = self._buffer, ""
        return ("", rest) if self._thinking else (rest, "")


def _partial_tag_suffix(text: str, tag: str) -> int:
    """Length of the longest end of `text` that could be the start of `tag`."""
    for size in range(min(len(text), len(tag) - 1), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


# --- Providers, cache, usage --------------------------------------------------------------


@dataclass
class _Provider:
    name: str
    base_url: str
    keys: list[str]
    thinking_style: str
    headers: dict[str, str] = field(default_factory=dict)
    _next: int = 0
    _cool_until: dict[str, float] = field(default_factory=dict)

    def key(self) -> str:
        """Next key, round-robin, skipping keys that were just rate limited if another is free."""
        now = time.monotonic()
        for _ in range(len(self.keys)):
            key = self.keys[self._next % len(self.keys)]
            self._next += 1
            if self._cool_until.get(key, 0.0) <= now:
                return key
        return min(self.keys, key=lambda k: self._cool_until.get(k, 0.0))

    def cool(self, key: str, seconds: float) -> None:
        self._cool_until[key] = time.monotonic() + seconds


class LLMCache:
    """Responses keyed by request, so simulation and test reruns cost nothing."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
            " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, value) VALUES (?, ?)", (key, value)
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class UsageMeter:
    """Running totals per job for this process. Per-household totals go to the llm_usage table."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )

    def add(self, result: LLMResult) -> None:
        with self._lock:
            totals = self._totals[result.job]
            totals["calls"] += 1
            if result.cached:
                totals["cached"] += 1
            else:
                totals["prompt_tokens"] += result.usage.prompt_tokens
                totals["completion_tokens"] += result.usage.completion_tokens

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {job: dict(totals) for job, totals in self._totals.items()}


# --- Client ---------------------------------------------------------------------------------


class LLMClient:
    def __init__(
        self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = s = settings or get_settings()
        self._providers: dict[str, _Provider] = {}  # in fallback order
        if s.nvidia_keys:
            self._providers["nvidia"] = _Provider(
                "nvidia", s.nvidia_base_url.rstrip("/"), s.nvidia_keys, s.nvidia_thinking_style
            )
        if s.openrouter_api_key:
            self._providers["openrouter"] = _Provider(
                "openrouter",
                s.openrouter_base_url.rstrip("/"),
                [s.openrouter_api_key],
                s.openrouter_thinking_style,
                dict(OPENROUTER_HEADERS),
            )
        self._sem = {name: asyncio.Semaphore(s.llm_max_concurrency) for name in PROVIDERS}
        self._http = httpx.AsyncClient(transport=transport, timeout=self._timeout(None))
        self._cache: LLMCache | None = None
        self.meter = UsageMeter()

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._cache is not None:
            self._cache.close()

    async def chat(
        self,
        job: str,
        messages: list[dict[str, Any]],
        *,
        thinking: bool | None = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        cache: bool = False,
        provider: str | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        """One reply, not streamed. thinking=None leaves the model's default.

        cache=True returns a stored reply for an identical request (used by simulations).
        provider="nvidia" or "openrouter" skips the fallback.
        """
        opts = dict(
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_body=extra_body,
        )
        route = self._route(provider)
        cache_key = self._cache_key(job, messages, opts) if cache else None
        if cache_key:
            hit = self._get_cache().get(cache_key)
            if hit is not None:
                result = LLMResult.from_cache(hit)
                result.cached = True
                self.meter.add(result)
                return result
        errors = []
        for p in route:
            body = self._body(p, job, messages, opts, stream=False)
            try:
                result = await self._complete(p, job, body, timeout)
            except _ProviderFailed as exc:
                errors.append(f"{p.name}: {exc}")
                log.warning("LLM %s failed for job=%s: %s", p.name, job, exc)
                continue
            if cache_key:
                self._get_cache().put(cache_key, result.to_cache())
            self.meter.add(result)
            return result
        raise LLMError("; ".join(errors))

    def stream(
        self,
        job: str,
        messages: list[dict[str, Any]],
        *,
        thinking: bool | None = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        provider: str | None = None,
        timeout: float | None = None,
        first_chunk_timeout: float | None = None,
    ) -> ChatStream:
        """A streamed reply; use with `async with`. Falls back if a provider fails before its
        first chunk (after that, an error ends the stream)."""
        opts = dict(
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_body=extra_body,
        )
        attempts = [(p, self._body(p, job, messages, opts, stream=True)) for p in self._route(provider)]
        return ChatStream(self, job, attempts, timeout, first_chunk_timeout)

    async def list_models(self, provider: str, contains: str = "nemotron") -> list[str]:
        """Model IDs a provider offers. Neither catalog needs a key."""
        base = {"nvidia": self.settings.nvidia_base_url, "openrouter": self.settings.openrouter_base_url}
        resp = await self._http.get(f"{base[provider].rstrip('/')}/models", timeout=self._timeout(20))
        resp.raise_for_status()
        return sorted(
            m["id"] for m in resp.json().get("data", []) if contains.lower() in m["id"].lower()
        )

    # --- internals ---

    def _route(self, provider: str | None) -> list[_Provider]:
        if provider is not None and provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if provider is None:
            route = list(self._providers.values())
        elif provider in self._providers:
            route = [self._providers[provider]]
        else:
            raise LLMError(f"{provider} is not configured: add its API key to .env")
        if not route:
            raise LLMError(
                "no LLM provider configured: set NVIDIA_API_KEYS and/or OPENROUTER_API_KEY in .env"
            )
        return route

    def _timeout(self, read_s: float | None) -> httpx.Timeout:
        s = self.settings
        return httpx.Timeout(read_s or s.llm_timeout_s, connect=s.llm_connect_timeout_s)

    @staticmethod
    def _headers(p: _Provider, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", **p.headers}

    def _body(
        self, p: _Provider, job: str, messages: list[dict[str, Any]], opts: dict[str, Any], stream: bool
    ) -> dict[str, Any]:
        thinking = opts["thinking"]
        body: dict[str, Any] = {
            "model": self.settings.model_for(p.name, job),
            "messages": _with_thinking_tag(messages, p.thinking_style, thinking),
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        for name in ("tools", "tool_choice", "temperature", "max_tokens", "response_format"):
            if opts[name] is not None:
                body[name] = opts[name]
        if thinking is not None:
            if p.thinking_style == "chat_template_kwargs":
                body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
            elif p.thinking_style == "reasoning":
                body["reasoning"] = {"enabled": bool(thinking)}
        body.update(opts["extra_body"] or {})
        return body

    async def _complete(
        self, p: _Provider, job: str, body: dict[str, Any], timeout: float | None
    ) -> LLMResult:
        problem = "no attempt made"
        for attempt in range(2):  # first try plus one retry
            key = p.key()
            started = time.perf_counter()
            try:
                async with self._sem[p.name]:
                    resp = await self._http.post(
                        f"{p.base_url}/chat/completions",
                        json=body,
                        headers=self._headers(p, key),
                        timeout=self._timeout(timeout),
                    )
            except httpx.HTTPError as exc:
                problem = f"{type(exc).__name__}: {exc}"[:200]
            else:
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        problem = "reply was not JSON"
                    else:
                        if data.get("choices"):
                            latency = (time.perf_counter() - started) * 1000
                            return _parse_completion(data, job, p.name, body["model"], latency, resp.headers)
                        problem = f"no choices in reply: {str(data)[:200]}"
                else:
                    problem = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if resp.status_code == 429:
                        p.cool(key, _retry_after(resp))
                    if resp.status_code not in RETRYABLE_STATUS:
                        break
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY_S)
        raise _ProviderFailed(problem)

    def _cache_key(self, job: str, messages: list[dict[str, Any]], opts: dict[str, Any]) -> str:
        models = {p: self.settings.model_for(p, job) for p in PROVIDERS}
        blob = json.dumps(
            {"job": job, "models": models, "messages": messages, **opts}, sort_keys=True, default=str
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _get_cache(self) -> LLMCache:
        if self._cache is None:
            self._cache = LLMCache(self.settings.llm_cache_path)
        return self._cache


class ChatStream:
    """A streamed reply.

        async with llm.stream("voice", messages) as stream:
            async for delta in stream:
                ...
        stream.result  # LLMResult with timing and usage, set on exit
    """

    def __init__(
        self,
        client: LLMClient,
        job: str,
        attempts: list[tuple[_Provider, dict[str, Any]]],
        timeout: float | None,
        first_chunk_timeout: float | None,
    ) -> None:
        self._client = client
        self._job = job
        self._attempts = attempts
        self._timeout = timeout
        self._first_chunk_timeout = first_chunk_timeout or timeout or client.settings.llm_timeout_s
        self._resp: httpx.Response | None = None
        self._events: AsyncIterator[dict[str, Any]] | None = None
        self._first: dict[str, Any] | None = None
        self._held: str | None = None  # the provider whose semaphore slot we hold
        self._splitter = ThinkSplitter()
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._finish: str | None = None
        self._usage: dict[str, Any] = {}
        self._rate_limit: dict[str, str] = {}
        self._started = 0.0
        self._ttft: float | None = None
        self._ttft_content: float | None = None
        self.provider = ""
        self.model = ""
        self.result: LLMResult | None = None

    async def __aenter__(self) -> ChatStream:
        errors = []
        for p, body in self._attempts:
            problem = await self._open(p, body)
            if problem is None:
                return self
            errors.append(f"{p.name}: {problem}")
            log.warning("LLM stream %s failed for job=%s: %s", p.name, self._job, problem)
        raise LLMError("; ".join(errors))

    async def _open(self, p: _Provider, body: dict[str, Any]) -> str | None:
        """Try one provider, with one retry. Returns None once the first chunk arrives."""
        client = self._client
        problem = "no attempt made"
        limited = 0  # rate-limit replies seen; they get one extra quick try (per-minute limits clear fast)
        attempt = 0
        while attempt < 2 + min(limited, 1):
            key = p.key()
            retry = True
            resp: httpx.Response | None = None
            await client._sem[p.name].acquire()
            self._started = time.perf_counter()
            try:
                request = client._http.build_request(
                    "POST",
                    f"{p.base_url}/chat/completions",
                    json=body,
                    headers=client._headers(p, key),
                    timeout=client._timeout(self._timeout),
                )
                resp = await client._http.send(request, stream=True)
                if resp.status_code == 200:
                    events = _sse_events(resp.aiter_lines())
                    first = await asyncio.wait_for(events.__anext__(), self._first_chunk_timeout)
                    self._resp, self._events, self._first = resp, events, first
                    self._held, self.provider, self.model = p.name, p.name, body["model"]
                    self._rate_limit = _rate_limit_headers(resp.headers)
                    return None
                text = (await resp.aread()).decode(errors="replace")
                problem = f"HTTP {resp.status_code}: {text[:200]}"
                if resp.status_code == 429:
                    p.cool(key, _retry_after(resp))
                    limited += 1
                retry = resp.status_code in RETRYABLE_STATUS
            except _StreamError as exc:
                problem, retry = f"error in stream: {exc}", False
            except TimeoutError:
                # Nothing arrived in time. Go straight to the next provider: on a phone call, a
                # second wait on the same slow provider costs the caller seconds of silence.
                problem, retry = f"no first chunk within {self._first_chunk_timeout:g} s", False
            except (httpx.HTTPError, StopAsyncIteration) as exc:
                problem = f"{type(exc).__name__}: {exc}"[:200]
            if resp is not None:
                await resp.aclose()
            client._sem[p.name].release()
            if not retry:
                break
            attempt += 1
            if attempt < 2 + min(limited, 1):
                await asyncio.sleep(RETRY_DELAY_S)
        return problem

    def __aiter__(self) -> AsyncIterator[StreamDelta]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StreamDelta]:
        if self._first is not None:
            first, self._first = self._first, None
            yield self._take(first)
        if self._events is not None:
            async for chunk in self._events:
                yield self._take(chunk)
        text, reasoning = self._splitter.flush()
        if text or reasoning:
            yield StreamDelta(chunk={}, text=text, reasoning=reasoning)

    def _take(self, chunk: dict[str, Any]) -> StreamDelta:
        elapsed = (time.perf_counter() - self._started) * 1000
        if chunk.get("usage"):
            self._usage = chunk["usage"]
        text = reasoning = ""
        tool_delta = False
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self._content.append(delta["content"])
                text, reasoning = self._splitter.feed(delta["content"])
            separate = delta.get("reasoning_content") or delta.get("reasoning")
            if separate:
                self._reasoning.append(separate)
                reasoning += separate
            for call in delta.get("tool_calls") or []:
                self._merge_tool_call(call)
                tool_delta = True
            if choice.get("finish_reason"):
                self._finish = choice["finish_reason"]
        if self._ttft is None and (text or reasoning or tool_delta):
            self._ttft = elapsed
        if self._ttft_content is None and (text.strip() or tool_delta):
            self._ttft_content = elapsed
        return StreamDelta(chunk=chunk, text=text, reasoning=reasoning)

    def _merge_tool_call(self, call: dict[str, Any]) -> None:
        slot = self._tool_calls.setdefault(
            call.get("index", 0),
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if call.get("id"):
            slot["id"] = call["id"]
        function = call.get("function") or {}
        if function.get("name") and not slot["function"]["name"]:
            slot["function"]["name"] = function["name"]
        if function.get("arguments"):
            slot["function"]["arguments"] += function["arguments"]

    async def __aexit__(self, *exc_info: object) -> None:
        if self._events is not None:
            await self._events.aclose()
        if self._resp is not None:
            await self._resp.aclose()
        if self._held is not None:
            self._client._sem[self._held].release()
            self._held = None
        answer, inline_reasoning = split_think("".join(self._content))
        reasoning = "\n".join(r for r in ("".join(self._reasoning).strip(), inline_reasoning) if r)
        self.result = LLMResult(
            text=answer,
            reasoning=reasoning,
            tool_calls=[self._tool_calls[i] for i in sorted(self._tool_calls)],
            finish_reason=self._finish,
            job=self._job,
            provider=self.provider,
            model=self.model,
            usage=Usage(
                int(self._usage.get("prompt_tokens") or 0),
                int(self._usage.get("completion_tokens") or 0),
            ),
            latency_ms=(time.perf_counter() - self._started) * 1000,
            ttft_ms=self._ttft,
            ttft_content_ms=self._ttft_content,
            rate_limit=self._rate_limit,
        )
        self._client.meter.add(self.result)


# --- helpers ------------------------------------------------------------------------------


def record_usage(db: Database, result: LLMResult, household_id: int | None = None) -> None:
    """Save one model call's tokens and timing to the run's llm_usage table."""
    from app.models import LLMUsage

    with db.session() as session:
        session.add(
            LLMUsage(
                job=result.job,
                provider=result.provider,
                model=result.model,
                household_id=household_id,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                ttft_ms=result.ttft_ms,
                latency_ms=result.latency_ms,
                cached=result.cached,
            )
        )
        session.commit()


def _with_thinking_tag(
    messages: list[dict[str, Any]], style: str, thinking: bool | None
) -> list[dict[str, Any]]:
    """For models switched by a system-prompt tag (/think, /no_think)."""
    if style != "system_tag" or thinking is None:
        return messages
    tag = "/think" if thinking else "/no_think"
    messages = [dict(m) for m in messages]
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = f"{tag}\n{messages[0].get('content') or ''}".rstrip()
    else:
        messages.insert(0, {"role": "system", "content": tag})
    return messages


def _parse_completion(
    data: dict[str, Any], job: str, provider: str, model: str, latency_ms: float, headers: httpx.Headers
) -> LLMResult:
    choice = data["choices"][0]
    message = choice.get("message") or {}
    answer, inline_reasoning = split_think(message.get("content") or "")
    separate = message.get("reasoning_content") or message.get("reasoning") or ""
    usage = data.get("usage") or {}
    return LLMResult(
        text=answer,
        reasoning="\n".join(r for r in (separate.strip(), inline_reasoning) if r),
        tool_calls=message.get("tool_calls") or [],
        finish_reason=choice.get("finish_reason"),
        job=job,
        provider=provider,
        model=data.get("model") or model,
        usage=Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
        latency_ms=latency_ms,
        rate_limit=_rate_limit_headers(headers),
    )


async def _sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """OpenAI-style server-sent events, parsed. Skips blank lines and keep-alive comments."""
    async for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            log.debug("skipping malformed stream line: %.120s", data)
            continue
        if isinstance(chunk, dict) and chunk.get("error") and not chunk.get("choices"):
            raise _StreamError(str(chunk["error"])[:200])
        yield chunk


def _retry_after(resp: httpx.Response, default: float = 5.0) -> float:
    try:
        return min(max(float(resp.headers.get("retry-after", default)), 0.0), 60.0)
    except ValueError:
        return default


def _rate_limit_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
