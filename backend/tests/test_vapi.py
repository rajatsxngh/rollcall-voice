"""Vapi endpoints, the voice spec and the setup scripts, against a fake model: no network, no keys."""
import asyncio
import json
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.agent import voice_spec, voice_tools
from app.llm import LLMClient
from app.main import create_app
from app.models import Household, LLMUsage, Need
from scripts import call_me, vapi_setup

PRD_OPENING = (
    "Hi, this is RollCall, an automated assistant calling for State Farm about the tornado warning"
    " near you. Is this Maria? Are you safe right now?"
)
POWER_NEED = {"category": "power", "details": "oxygen machine on battery", "urgency": "P2"}


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr("app.llm.RETRY_DELAY_S", 0)


class FakeModel:
    """Stands in for build.nvidia.com and records each request body."""

    def __init__(self, respond):
        self.respond = respond
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return self.respond(request)


def use_model(client, respond) -> FakeModel:
    """Point the running app's LLM client at a fake NVIDIA endpoint."""
    fake = FakeModel(respond)
    settings = client.app.state.settings.model_copy(update={"nvidia_api_keys": "nv-test"})
    old = client.app.state.llm
    client.app.state.llm = LLMClient(settings, transport=httpx.MockTransport(fake))
    client.portal.call(old.aclose)
    return fake


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def vapi_turn(*messages, **extra):
    """A custom-LLM request shaped like Vapi's, with the extra fields Vapi adds."""
    body = {
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "messages": [*voice_spec.start_messages("Maria"), *messages],
        "temperature": 0.3,
        "max_tokens": 300,
        "stream": True,
        "call": {
            "id": "call-1",
            "assistantOverrides": {"variableValues": {"name": "Maria"}, "metadata": {"household_id": 7}},
        },
        "metadata": {"household_id": 7},
        "customer": {"number": "+15550100001", "name": "Maria"},
        "phoneNumber": {"id": "pn-1"},
    }
    body.update(extra)
    return body


def read_sse(resp):
    """The parsed chunks of an SSE reply, which must end with data: [DONE]."""
    assert resp.headers["content-type"].startswith("text/event-stream")
    lines = [line[len("data: ") :] for line in resp.text.split("\n") if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def spoken(chunks):
    return "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)


def events_of(client, event_type):
    return [e for e in client.get("/events").json()["events"] if e["type"] == event_type]


def add_household(client) -> int:
    with client.app.state.db.session() as session:
        home = Household(name="Maria Lopez", phone="+15550100001", address="1412 Oak St", lat=32.95, lng=-96.7, age=81)
        session.add(home)
        session.commit()
        return home.id


def tool_calls_message(*calls, household_id=None, call_id="call-1"):
    overrides = {"variableValues": {"name": "Maria"}}
    if household_id is not None:
        overrides["metadata"] = {"household_id": household_id}
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "assistantOverrides": overrides},
            "toolCallList": [
                {"id": f"tc{i}", "type": "function", "function": {"name": name, "arguments": arguments}}
                for i, (name, arguments) in enumerate(calls, 1)
            ],
        }
    }


# --- The shared voice spec -------------------------------------------------------------------


def test_voice_spec_carries_the_prd_call_design():
    assert voice_spec.render(voice_spec.FIRST_MESSAGE, name="Maria") == PRD_OPENING
    prompt = voice_spec.SYSTEM_PROMPT
    positions = [prompt.index(q) for q in voice_spec.QUESTIONS]
    assert positions == sorted(positions)  # the five questions, in order
    for rule in (
        "automated assistant",
        "skipping anything already answered",
        "Never give medical advice",
        "Please hang up and call 911",
        "Never accept \"I'm fine\"",
        "medical devices that need power and who is home",
        "downplaying",
        "under 2 minutes",
        "one or two short sentences",
    ):
        assert rule in prompt
    for level in ("P1 Emergency", "P2 Urgent", "P3 Needs help", "P4 Safe"):
        assert level in prompt
    assert voice_spec.TOOL_NAMES == ("record_need", "flag_urgent", "transfer_to_human", "send_text", "end_call")
    for tool in voice_spec.TOOLS:
        parameters = tool["function"]["parameters"]
        assert tool["type"] == "function" and parameters["type"] == "object"
        assert set(parameters["required"]) <= set(parameters["properties"])
    assert set(voice_tools.HANDLERS) == set(voice_spec.TOOL_NAMES)
    assert json.loads(json.dumps(voice_spec.TOOLS)) == voice_spec.TOOLS  # plain data
    system, opening = voice_spec.start_messages("Maria")
    assert "{{" not in system["content"] and opening == {"role": "assistant", "content": PRD_OPENING}


# --- POST /vapi/chat/completions ---------------------------------------------------------------


def test_chat_streams_openai_chunks_then_done(client):
    fake = use_model(
        client,
        lambda r: sse(
            delta(role="assistant", content=""),
            delta(content="Glad you're safe."),
            delta(content=" Is anyone hurt?"),
            finish("stop"),
            {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 9}},
        ),
    )
    resp = client.post("/vapi/chat/completions", json=vapi_turn({"role": "user", "content": "Yes, I'm okay."}))
    assert resp.status_code == 200
    chunks = read_sse(resp)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert spoken(chunks) == "Glad you're safe. Is anyone hurt?"
    assert chunks[-1]["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}

    body = fake.bodies[0]  # the voice model, thinking off, on Vapi's messages with the spec's tools
    assert body["model"] == client.app.state.settings.voice_model
    assert body["stream"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert (body["max_tokens"], body["temperature"]) == (300, 0.3)
    assert body["tools"] == voice_spec.TOOLS
    assert body["messages"][0]["role"] == "system" and "Maria" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "Yes, I'm okay."}
    assert not {"call", "metadata", "customer", "phoneNumber"} & set(body)


def test_each_turn_records_time_to_first_answer(client):
    use_model(
        client,
        lambda r: sse(delta(content="Is anyone hurt?"), {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 5}}),
    )
    client.post("/vapi/chat/completions", json=vapi_turn())
    with client.app.state.db.session() as session:
        [row] = session.exec(select(LLMUsage)).all()
    assert (row.job, row.provider, row.household_id) == ("voice", "nvidia", 7)
    assert (row.prompt_tokens, row.completion_tokens) == (900, 5)
    assert row.ttft_ms is not None and row.ttft_ms >= 0


def test_reasoning_is_never_spoken(client):
    use_model(
        client,
        lambda r: sse(
            delta(role="assistant", reasoning_content="She says she is fine but"),
            delta(content="<thi"),
            delta(content="nk>ask about the oxygen</th"),
            delta(content="ink>Does anyone there"),
            delta(content=" use a medical device?"),
            finish("stop"),
        ),
    )
    resp = client.post("/vapi/chat/completions", json=vapi_turn({"role": "user", "content": "I'm fine, honey."}))
    assert spoken(read_sse(resp)) == "Does anyone there use a medical device?"
    for leaked in ("think", "oxygen", "she is fine", "reasoning"):
        assert leaked not in resp.text


def test_tool_call_deltas_are_forwarded(client):
    fake = use_model(
        client,
        lambda r: sse(
            delta(content="Okay, I'm noting that."),
            delta(tool_calls=[{"index": 0, "id": "call_abc", "type": "function",
                               "function": {"name": "record_need", "arguments": ""}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"category": "power", '}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '"details": "oxygen machine on battery", "urgency": "P2"}'}}]),
            finish("tool_calls"),
        ),
    )
    vapi_tools = [voice_spec.TOOLS[0]]
    resp = client.post(
        "/vapi/chat/completions",
        json=vapi_turn({"role": "user", "content": "My oxygen machine is on battery."}, tools=vapi_tools),
    )
    chunks = read_sse(resp)
    calls = [call for c in chunks for call in c["choices"][0]["delta"].get("tool_calls", [])]
    assert calls[0] == {"index": 0, "id": "call_abc", "type": "function", "function": {"name": "record_need", "arguments": ""}}
    assert all(set(call) == {"index", "function"} and "name" not in call["function"] for call in calls[1:])
    assert json.loads("".join(call["function"].get("arguments", "") for call in calls)) == POWER_NEED
    assert spoken(chunks) == "Okay, I'm noting that."
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert fake.bodies[0]["tools"] == vapi_tools  # tools Vapi sends win over the spec's copy


@pytest.mark.parametrize(
    "respond",
    [
        lambda r: httpx.Response(500, text="model down"),  # every attempt fails: LLMError
        lambda r: sse(delta(content="<think>only reasoning, no answer</think>"), finish("stop")),
    ],
    ids=["model-error", "empty-answer"],
)
def test_a_failed_turn_speaks_the_fallback_line(client, respond):
    use_model(client, respond)
    resp = client.post("/vapi/chat/completions", json=vapi_turn({"role": "user", "content": "Hello?"}))
    assert resp.status_code == 200
    chunks = read_sse(resp)
    assert spoken(chunks) == voice_spec.FALLBACK_REPLY
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_a_provider_that_never_starts_answering_is_dropped_quickly(client, monkeypatch):
    monkeypatch.setattr("app.routers.vapi.FIRST_CHUNK_TIMEOUT_S", 0.05)

    async def stalled():
        await asyncio.sleep(5)
        yield b"data: [DONE]\n\n"

    async def respond(request):
        return httpx.Response(200, content=stalled(), headers={"content-type": "text/event-stream"})

    fake = use_model(client, respond)
    started = time.perf_counter()
    resp = client.post("/vapi/chat/completions", json=vapi_turn({"role": "user", "content": "Hello?"}))
    assert spoken(read_sse(resp)) == voice_spec.FALLBACK_REPLY
    assert time.perf_counter() - started < 2
    assert len(fake.bodies) == 1  # no second wait on a stalled provider, straight to the fallback line


def test_no_model_configured_still_answers(client):
    # The test settings have no keys, so the LLM client raises before any request.
    resp = client.post("/vapi/chat/completions", json={"messages": [{"role": "user", "content": "Hi"}], "stream": True})
    assert spoken(read_sse(resp)) == voice_spec.FALLBACK_REPLY


def test_stream_false_gets_one_completion(client):
    fake = use_model(
        client,
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "<think>plan</think>Is anyone hurt?"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        ),
    )
    body = client.post("/vapi/chat/completions", json=vapi_turn(stream=False)).json()
    assert fake.bodies[0]["stream"] is False
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Is anyone hurt?"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 8


# --- POST /vapi/webhook -------------------------------------------------------------------------


def test_webhook_answers_tool_calls_in_vapi_format(client):
    resp = client.post("/vapi/webhook", json=tool_calls_message(("record_need", POWER_NEED)))
    assert resp.status_code == 200
    [result] = resp.json()["results"]
    assert (result["name"], result["toolCallId"]) == ("record_need", "tc1")
    assert isinstance(result["result"], str) and result["result"]


def test_record_need_without_a_household_still_reaches_the_feed(client):
    # Arguments may also arrive as a JSON string (the OpenAI wire format).
    client.post("/vapi/webhook", json=tool_calls_message(("record_need", json.dumps(POWER_NEED))))
    [event] = events_of(client, "need.recorded")
    assert event["household_id"] is None
    payload = event["payload"]
    assert (payload["category"], payload["details"], payload["urgency"]) == ("power", "oxygen machine on battery", "P2")
    assert payload["text"] == "Recorded a need for Maria (test call): oxygen machine on battery (power, P2 urgent)."
    with client.app.state.db.session() as session:
        assert session.exec(select(Need)).all() == []


def test_record_need_stores_a_need_when_the_household_exists(client):
    household_id = add_household(client)
    client.post("/vapi/webhook", json=tool_calls_message(("record_need", POWER_NEED), household_id=household_id))
    with client.app.state.db.session() as session:
        [need] = session.exec(select(Need)).all()
    assert (need.household_id, need.category, need.details, need.urgency) == (
        household_id, "power", "oxygen machine on battery", "P2"
    )
    [event] = events_of(client, "need.recorded")
    assert (event["household_id"], event["payload"]["need_id"]) == (household_id, need.id)
    assert event["payload"]["text"].startswith("Recorded a need for Maria Lopez, 1412 Oak St:")


def test_a_repeated_tool_call_delivery_runs_the_tool_once(client):
    message = tool_calls_message(("record_need", POWER_NEED))
    assert client.post("/vapi/webhook", json=message).json() == client.post("/vapi/webhook", json=message).json()
    assert len(events_of(client, "need.recorded")) == 1


def test_stub_tools_answer_and_post_a_feed_line(client):
    resp = client.post(
        "/vapi/webhook",
        json=tool_calls_message(
            ("flag_urgent", {"level": "P1", "reason": "smells gas"}),
            ("transfer_to_human", {"reason": "asked for a person"}),
            ("send_text", {"message": "Help is on the way."}),
            ("end_call", {"summary": "Maria smells gas; told her to call 911."}),
            ("no_such_tool", {}),
        ),
    )
    results = resp.json()["results"]
    assert [r["toolCallId"] for r in results] == ["tc1", "tc2", "tc3", "tc4", "tc5"]
    assert all(isinstance(r.get("result"), str) and r["result"] for r in results[:4])
    assert "error" in results[4] and "result" not in results[4]
    notes = events_of(client, "agent.note")
    assert [n["payload"]["tool"] for n in notes] == ["flag_urgent", "transfer_to_human", "send_text", "end_call"]
    assert notes[0]["payload"]["text"] == "Flagged Maria (test call) as P1 emergency: smells gas."


def test_server_messages_become_call_events(client):
    household_id = add_household(client)
    call = {"id": "call-9", "assistantOverrides": {"metadata": {"household_id": household_id}}}
    for message in (
        {"type": "status-update", "status": "ringing"},
        {"type": "status-update", "status": "in-progress"},  # already announced
        {"type": "transcript", "role": "assistant", "transcriptType": "final", "transcript": "Are you safe right now?"},
        {"type": "transcript", "role": "user", "transcriptType": "partial", "transcript": "Yes I"},
        {"type": "transcript", "role": "user", "transcriptType": "final", "transcript": "Yes, I'm safe."},
        {"type": "tool-calls", "toolCallList": [
            {"id": "tc1", "type": "function", "function": {"name": "end_call", "arguments": {"summary": "Maria is safe."}}}
        ]},
        {"type": "status-update", "status": "ended", "endedReason": "assistant-said-end-call-phrase"},
        {"type": "end-of-call-report", "endedReason": "assistant-said-end-call-phrase", "analysis": {},
         "artifact": {"transcript": "AI: Are you safe right now? User: Yes, I'm safe."},
         "startedAt": "2026-11-08T03:00:00.000Z", "endedAt": "2026-11-08T03:01:30.500Z"},
    ):
        assert client.post("/vapi/webhook", json={"message": {"call": call, **message}}).status_code == 200

    calls = [e for e in client.get("/events").json()["events"] if e["type"].startswith("call.")]
    assert [e["type"] for e in calls] == ["call.started", "call.transcript", "call.transcript", "call.ended"]
    assert all(e["household_id"] == household_id for e in calls)
    started, agent_line, resident_line, ended = (e["payload"] for e in calls)
    assert started == {"call_id": "call-9", "channel": "vapi", "attempt": 1, "status": "ringing"}
    assert agent_line == {"call_id": "call-9", "role": "agent", "line": "Are you safe right now?"}
    assert resident_line == {"call_id": "call-9", "role": "resident", "line": "Yes, I'm safe."}
    assert ended == {
        "call_id": "call-9",
        "outcome": "completed",
        "summary": "Maria is safe.",
        "ended_reason": "assistant-said-end-call-phrase",
        "duration_s": 90.5,
    }


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [("customer-did-not-answer", "no_answer"), ("customer-ended-call", "hung_up"),
     ("assistant-forwarded-call", "transferred"), ("pipeline-error-custom-llm-llm-failed", "failed")],
)
def test_call_outcome_comes_from_the_ended_reason(client, reason, outcome):
    client.post("/vapi/webhook", json={"message": {"type": "end-of-call-report", "endedReason": reason, "call": {"id": "c"}}})
    [ended] = events_of(client, "call.ended")
    assert ended["payload"]["outcome"] == outcome and ended["household_id"] is None


def test_secret_is_required_when_set(settings):
    with TestClient(create_app(settings.model_copy(update={"vapi_webhook_secret": "s3cret"}))) as client:
        message = {"message": {"type": "status-update", "status": "ringing", "call": {"id": "c"}}}
        assert client.post("/vapi/webhook", json=message).status_code == 401
        assert client.post("/vapi/webhook", json=message, headers={"X-Vapi-Secret": "wrong"}).status_code == 401
        assert client.post("/vapi/chat/completions", json={"messages": []}).status_code == 401
        assert client.post("/vapi/webhook", json=message, headers={"X-Vapi-Secret": "s3cret"}).status_code == 200
        assert client.post("/vapi/webhook", json=message, headers={"Authorization": "Bearer s3cret"}).status_code == 200


# --- scripts/vapi_setup.py and scripts/call_me.py --------------------------------------------------


def test_assistant_payload(settings):
    payload = vapi_setup.build_assistant(
        settings.model_copy(update={"vapi_webhook_secret": "s3cret"}), "https://rollcall.example.com/"
    )
    model = payload["model"]
    assert model["provider"] == "custom-llm"
    assert model["url"] == "https://rollcall.example.com/vapi"  # Vapi appends /chat/completions
    assert model["messages"] == [{"role": "system", "content": voice_spec.SYSTEM_PROMPT}]
    assert [t["function"] for t in model["tools"]] == [t["function"] for t in voice_spec.TOOLS]
    assert {t["type"] for t in model["tools"]} == {"function"}
    assert {t["server"]["url"] for t in model["tools"]} == {"https://rollcall.example.com/vapi/webhook"}
    assert payload["firstMessage"] == voice_spec.FIRST_MESSAGE
    assert payload["server"]["url"] == "https://rollcall.example.com/vapi/webhook"
    assert payload["serverMessages"] == [
        "tool-calls", "status-update", "transcript", "end-of-call-report"
    ]
    assert payload["maxDurationSeconds"] == 180
    assert payload["voice"] == {"provider": "vapi", "voiceId": "Elliot"}
    assert payload["transcriber"] == {"provider": "deepgram", "model": "nova-3", "language": "en"}
    assert payload["endCallPhrases"] == ["goodbye"]
    # The secret goes on every request Vapi makes to us, and is hidden when printed.
    assert model["headers"] == payload["server"]["headers"] == {"X-Vapi-Secret": "s3cret"}
    assert all(t["server"]["headers"] == {"X-Vapi-Secret": "s3cret"} for t in model["tools"])
    assert "s3cret" not in json.dumps(vapi_setup.redacted(payload))
    assert "headers" not in vapi_setup.build_assistant(settings, "https://x.example.com")["server"]


def test_call_payload(settings):
    configured = settings.model_copy(update={"vapi_assistant_id": "asst-1", "vapi_phone_number_id": "pn-1"})
    assert call_me.build_call(configured, "+12145551234", "Maria", household_id=12) == {
        "assistantId": "asst-1",
        "phoneNumberId": "pn-1",
        "customer": {"number": "+12145551234", "name": "Maria"},
        "assistantOverrides": {
            "variableValues": {"name": "Maria"},
            "metadata": {"source": "call_me", "household_id": 12},
        },
    }
    assert "household_id" not in call_me.build_call(configured, "+12145551234", "Maria")["assistantOverrides"]["metadata"]
    assert call_me.normalize_phone("(214) 555-1234") == "+12145551234"
    assert call_me.normalize_phone("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(ValueError):
        call_me.normalize_phone("12345")


def test_dry_runs_print_requests_without_calling_vapi(settings, monkeypatch, capsys):
    configured = settings.model_copy(
        update={"public_base_url": "https://rollcall.example.com", "vapi_assistant_id": "asst-1", "vapi_phone_number_id": "pn-1"}
    )

    def no_network(*args, **kwargs):
        raise AssertionError("a dry run must not call Vapi")

    monkeypatch.setattr(vapi_setup, "get_settings", lambda: configured)
    monkeypatch.setattr(call_me, "get_settings", lambda: configured)
    monkeypatch.setattr(httpx, "request", no_network)
    monkeypatch.setattr(httpx, "post", no_network)

    monkeypatch.setattr(sys, "argv", ["vapi_setup", "--dry-run"])
    assert vapi_setup.main() == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["model"]["url"] == "https://rollcall.example.com/vapi"
    assert "PATCH https://api.vapi.ai/assistant/asst-1" in out.err

    monkeypatch.setattr(sys, "argv", ["call_me", "214-555-1234", "Maria", "--household-id", "3", "--dry-run"])
    assert call_me.main() == 0
    out = capsys.readouterr()
    reminder, request = out.out.split("\n", 1)
    assert reminder == call_me.CONSENT_REMINDER
    body = json.loads(request)
    assert body["customer"]["number"] == "+12145551234"
    assert body["assistantOverrides"]["metadata"]["household_id"] == 3
    assert "POST https://api.vapi.ai/call" in out.err


# --- Voicemail and missing summaries ------------------------------------------------------------


def _send(client, call_id, *messages):
    for message in messages:
        assert client.post("/vapi/webhook", json={"message": {"call": {"id": call_id}, **message}}).status_code == 200


def test_talking_to_a_voicemail_greeting_is_a_no_answer(client):
    _send(
        client, "vm-1",
        {"type": "transcript", "role": "user", "transcriptType": "final",
         "transcript": "Nothing has been recorded. Record your message after the tone."},
        {"type": "tool-calls", "toolCallList": [
            {"id": "t1", "type": "function", "function": {"name": "end_call", "arguments": {"summary": "Not reached."}}}
        ]},
        {"type": "end-of-call-report", "endedReason": "assistant-said-end-call-phrase"},
    )
    [ended] = events_of(client, "call.ended")
    assert ended["payload"]["outcome"] == "no_answer"
    assert ended["payload"]["summary"] == "Reached voicemail; no one was checked on yet."


def test_vapi_voicemail_detection_is_a_no_answer(client):
    _send(client, "vm-2", {"type": "end-of-call-report", "endedReason": "voicemail"})
    [ended] = events_of(client, "call.ended")
    assert ended["payload"]["outcome"] == "no_answer"
    assert "voicemail" in ended["payload"]["summary"].lower()


def test_a_person_who_mentions_voicemail_is_still_checked_on(client):
    _send(
        client, "vm-3",
        {"type": "transcript", "role": "user", "transcriptType": "final", "transcript": "Sorry, I thought you were my voicemail."},
        {"type": "tool-calls", "toolCallList": [
            {"id": "t2", "type": "function", "function": {"name": "record_need", "arguments":
                {"category": "medical", "details": "oxygen on battery", "urgency": "P2"}}}
        ]},
        {"type": "end-of-call-report", "endedReason": "assistant-said-end-call-phrase"},
    )
    [ended] = events_of(client, "call.ended")
    assert ended["payload"]["outcome"] == "completed"


def test_a_call_without_end_call_gets_a_summary_from_its_tools(client):
    _send(
        client, "sum-1",
        {"type": "tool-calls", "toolCallList": [
            {"id": "s1", "type": "function", "function": {"name": "record_need", "arguments":
                {"category": "medical", "details": "oxygen machine stopped, lives alone", "urgency": "P1"}}},
            {"id": "s2", "type": "function", "function": {"name": "flag_urgent", "arguments": {"level": "P1", "reason": "oxygen"}}},
            {"id": "s3", "type": "function", "function": {"name": "transfer_to_human", "arguments": {"reason": "oxygen"}}},
        ]},
        {"type": "end-of-call-report", "endedReason": "assistant-said-end-call-phrase", "analysis": {}},
    )
    [ended] = events_of(client, "call.ended")
    assert ended["payload"]["summary"] == (
        "Ended without the agent's summary. Recorded: oxygen machine stopped, lives alone (P1); flagged P1;"
        " asked for a person; told a person will call back."
    )


def test_assistant_leaves_a_voicemail_and_hangs_up(settings):
    payload = vapi_setup.build_assistant(settings, "https://x.example.com")
    assert payload["voicemailDetection"]["provider"] == "vapi"
    assert "911" in payload["voicemailMessage"]
