"""LLM client tests against a fake transport: no network, no keys."""
import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, ThinkSplitter, split_think

NVIDIA = "integrate.api.nvidia.com"
OPENROUTER = "openrouter.ai"
HELLO = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr("app.llm.RETRY_DELAY_S", 0)


class FakeProviders:
    """Answers requests per host and records them."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes[request.url.host](request)

    @property
    def hosts(self):
        return [r.url.host for r in self.requests]

    def body(self, i):
        return json.loads(self.requests[i].content)


def make(tmp_path, routes, **overrides):
    values = dict(_env_file=None, data_dir=tmp_path, nvidia_api_keys="nv-1,nv-2", openrouter_api_key="or-1")
    values.update(overrides)
    fake = FakeProviders(routes)
    return LLMClient(Settings(**values), transport=httpx.MockTransport(fake)), fake


def completion(content, prompt_tokens=11, completion_tokens=5):
    return httpx.Response(
        200,
        json={
            "model": "served-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


async def test_answer_reasoning_and_usage(tmp_path):
    llm, fake = make(tmp_path, {NVIDIA: lambda r: completion("<think>check oxygen</think>Call Maria first.")})
    result = await llm.chat("manager", HELLO, thinking=True)
    await llm.aclose()
    assert result.text == "Call Maria first."
    assert result.reasoning == "check oxygen"
    assert (result.provider, result.usage.total_tokens) == ("nvidia", 16)
    body = fake.body(0)
    assert body["model"] == llm.settings.manager_model
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert llm.meter.snapshot()["manager"]["calls"] == 1


async def test_retries_once_then_falls_back_to_openrouter(tmp_path):
    llm, fake = make(
        tmp_path,
        {NVIDIA: lambda r: httpx.Response(503, text="busy"), OPENROUTER: lambda r: completion("ok")},
    )
    result = await llm.chat("voice", HELLO)
    await llm.aclose()
    assert result.provider == "openrouter"
    assert fake.hosts == [NVIDIA, NVIDIA, OPENROUTER]
    assert fake.body(2)["model"] == llm.settings.openrouter_voice_model
    assert fake.body(2)["reasoning"] == {"enabled": False}


async def test_auth_error_is_not_retried(tmp_path):
    llm, fake = make(
        tmp_path,
        {NVIDIA: lambda r: httpx.Response(401, text="bad key"), OPENROUTER: lambda r: completion("ok")},
    )
    await llm.chat("voice", HELLO)
    await llm.aclose()
    assert fake.hosts == [NVIDIA, OPENROUTER]


async def test_without_an_nvidia_key_it_goes_straight_to_openrouter(tmp_path):
    llm, fake = make(tmp_path, {OPENROUTER: lambda r: completion("ok")}, nvidia_api_keys="")
    result = await llm.chat("resident", HELLO)
    await llm.aclose()
    assert (result.provider, fake.hosts) == ("openrouter", [OPENROUTER])


async def test_no_provider_configured_raises(tmp_path):
    llm, _ = make(tmp_path, {}, nvidia_api_keys="", openrouter_api_key="")
    with pytest.raises(LLMError, match="no LLM provider"):
        await llm.chat("voice", HELLO)
    await llm.aclose()


async def test_all_providers_failing_raises_with_reasons(tmp_path):
    llm, _ = make(tmp_path, {NVIDIA: lambda r: httpx.Response(500), OPENROUTER: lambda r: httpx.Response(500)})
    with pytest.raises(LLMError, match="nvidia: HTTP 500.*openrouter: HTTP 500"):
        await llm.chat("voice", HELLO)
    await llm.aclose()


async def test_nvidia_keys_rotate(tmp_path):
    llm, fake = make(tmp_path, {NVIDIA: lambda r: completion("ok")})
    for _ in range(3):
        await llm.chat("voice", HELLO)
    await llm.aclose()
    assert [r.headers["authorization"] for r in fake.requests] == ["Bearer nv-1", "Bearer nv-2", "Bearer nv-1"]


async def test_system_tag_thinking_style(tmp_path):
    llm, fake = make(tmp_path, {NVIDIA: lambda r: completion("ok")}, nvidia_thinking_style="system_tag")
    await llm.chat("voice", [{"role": "system", "content": "Be brief."}, *HELLO], thinking=False)
    await llm.aclose()
    body = fake.body(0)
    assert body["messages"][0]["content"] == "/no_think\nBe brief."
    assert "chat_template_kwargs" not in body


async def test_cache_hit_skips_the_network(tmp_path):
    llm, fake = make(tmp_path, {NVIDIA: lambda r: completion("I'm fine, honey.")})
    first = await llm.chat("resident", HELLO, cache=True)
    second = await llm.chat("resident", HELLO, cache=True)
    await llm.aclose()
    assert (first.cached, second.cached, second.text) == (False, True, "I'm fine, honey.")
    assert len(fake.requests) == 1


async def test_stream_hides_reasoning_and_times_the_first_word(tmp_path):
    reply = sse(
        delta(role="assistant", content=""),
        delta(content="<thi"),
        delta(content="nk>hmm</think>Hel"),
        delta(content="lo"),
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
    )
    llm, fake = make(tmp_path, {NVIDIA: lambda r: reply})
    async with llm.stream("voice", HELLO) as stream:
        spoken = [d.text async for d in stream]
    await llm.aclose()
    assert "".join(spoken) == "Hello"
    result = stream.result
    assert (result.text, result.reasoning, result.usage.total_tokens) == ("Hello", "hmm", 13)
    assert result.ttft_ms is not None and result.ttft_content_ms is not None
    assert fake.body(0)["stream"] is True


async def test_stream_falls_back_before_the_first_chunk(tmp_path):
    llm, fake = make(tmp_path, {NVIDIA: lambda r: httpx.Response(500), OPENROUTER: lambda r: sse(delta(content="Hi"))})
    async with llm.stream("voice", HELLO) as stream:
        text = "".join([d.text async for d in stream])
    await llm.aclose()
    assert (text, stream.result.provider) == ("Hi", "openrouter")
    assert fake.hosts == [NVIDIA, NVIDIA, OPENROUTER]


async def test_stream_merges_tool_call_pieces(tmp_path):
    reply = sse(
        delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                           "function": {"name": "record_need", "arguments": '{"category": '}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '"power"}'}}]),
    )
    llm, _ = make(tmp_path, {NVIDIA: lambda r: reply})
    async with llm.stream("voice", HELLO) as stream:
        async for _ in stream:
            pass
    await llm.aclose()
    assert stream.result.tool_calls == [
        {"id": "call_1", "type": "function", "function": {"name": "record_need", "arguments": '{"category": "power"}'}}
    ]
    assert stream.result.ttft_content_ms is not None


def test_split_think_edge_cases():
    assert split_think("plan first</think>Answer") == ("Answer", "plan first")
    assert split_think("Answer<think>cut off") == ("Answer", "cut off")
    assert split_think("no tags") == ("no tags", "")


def test_think_splitter_handles_tags_split_across_chunks():
    splitter = ThinkSplitter()
    pieces = [splitter.feed(p) for p in ["A<", "thi", "nk>r1", "</th", "ink>B"]]
    answer = "".join(a for a, _ in pieces) + splitter.flush()[0]
    reasoning = "".join(r for _, r in pieces)
    assert (answer, reasoning) == ("AB", "r1")
