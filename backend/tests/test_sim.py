"""M7 simulator tests with mocked models: the voice and resident models answer from scripts, chosen
by the request's model id. No network, no keys."""
import json

import httpx
import pytest

from app.agent import voice_spec
from app.config import Settings
from app.engine.channels import CallResult, NeedReport
from app.llm import LLMClient
from app.models import CallOutcome, Channel, HiddenTruth, Household, NeedCategory, Persona, Urgency
from app.sim import batch
from app.sim.batch import City, run_batch, select_households, summarize
from app.sim.conversation import simulate_call
from app.sim.persona import HANGUP_MARKER, daughter_contact, resident_prompt
from app.sim.store import OutcomeStore, SimKey, StoredOutcome, store_path


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr("app.llm.RETRY_DELAY_S", 0)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, nvidia_api_keys="nv-test", resident_model="test/resident-model", nvidia_api_key="",
                    openrouter_api_key="")


# --- fake models -------------------------------------------------------------------------------


def tool(name, **arguments):
    return {"id": f"tc_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class FakeModels:
    """Routes each request to the voice or resident script by its model id. A script gets the
    request body and the turn number (how many lines the other side has said so far) and returns
    reply text or (text, [tool calls])."""

    def __init__(self, settings, voice, resident):
        self.voice_model, self.resident_model = settings.voice_model, settings.resident_model
        self.voice, self.resident = voice, resident
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        msgs = body["messages"]
        turn = sum(1 for m in msgs if m["role"] == "user")
        if body["model"] == self.voice_model:
            assert body["tools"] == voice_spec.TOOLS
            assert msgs[0]["content"].startswith("You are RollCall")
            reply = self.voice(body, turn)
        elif body["model"] == self.resident_model:
            assert "tools" not in body
            reply = self.resident(body, turn)
        else:
            raise AssertionError(f"unexpected model {body['model']}")
        text, calls = reply if isinstance(reply, tuple) else (reply, [])
        message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = calls
        return httpx.Response(200, json={
            "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        })

    def count(self, which):
        model = self.voice_model if which == "voice" else self.resident_model
        return sum(1 for b in self.requests if b["model"] == model)


def make_llm(settings, voice, resident):
    fake = FakeModels(settings, voice, resident)
    return LLMClient(settings, transport=httpx.MockTransport(fake)), fake


def household(hid=1, name="Dorothy Smith", **kw):
    values = dict(id=hid, name=name, phone=f"+1555{hid:07d}", address=f"{hid} Oak St, Richardson, TX",
                  lat=32.95, lng=-96.73, age=81, lives_alone=True, risk_score=50.0)
    values.update(kw)
    return Household(**values)


def truth(hid=1, urgency=Urgency.P4, needs=(), persona=Persona.NORMAL, answers=1):
    return HiddenTruth(household_id=hid, true_urgency=urgency, true_needs=list(needs),
                       persona_style=persona, answers_on_attempt=answers)


def chatty_resident(body, turn):
    return "I'm okay, the roof is leaking a bit."


def normal_agent(body, turn):
    """Asks one question, records a P3 need, then ends the call."""
    last = body["messages"][-1]
    if last["role"] == "tool":
        return "Thanks. A crew will come check the roof tomorrow."
    if turn == 1:
        return "Glad you're safe. Is your home damaged?"
    if turn == 2:
        return "", [tool("record_need", category="home_damage", details="roof leak", urgency="P3")]
    return "", [tool("end_call", summary="Spoke with Dorothy: roof leak, P3; crew tomorrow.")]


# --- persona prompts ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona, expect", [
    (Persona.NORMAL, "normal person"),
    (Persona.DOWNPLAYS, "I'm fine, honey"),
    (Persona.HARD_OF_HEARING, "repeat"),
    (Persona.SPANISH_ONLY, "only Spanish"),
    (Persona.CONFUSED, "lose track"),
    (Persona.ANGRY, "eventually"),
    (Persona.CALL_DAUGHTER, "555-01"),
    (Persona.HANGS_UP, HANGUP_MARKER),
])
def test_resident_prompt_covers_each_persona(persona, expect):
    h = household(medical_device="oxygen concentrator", kids_under_5=0)
    prompt = resident_prompt(h, truth(urgency=Urgency.P2, needs=["power:oxygen concentrator on battery, about 2 hours left"],
                                      persona=persona))
    assert expect in prompt
    assert "oxygen concentrator on battery" in prompt and "live alone" in prompt
    assert "P2" not in prompt  # the situation is shown as behavior, not as a label
    assert "Never say or hint that this is a simulation" in prompt
    if persona is Persona.CALL_DAUGHTER:
        assert daughter_contact(h)[1] in prompt


# --- conversations -----------------------------------------------------------------------------


async def test_normal_call_ends_with_end_call(settings):
    llm, fake = make_llm(settings, normal_agent, chatty_resident)
    h = household()
    run = await simulate_call(llm, h, truth(urgency=Urgency.P3, needs=["home_damage:roof leak"]))
    r = run.result
    assert isinstance(r, CallResult) and r.channel is Channel.SIM and r.attempt == 1
    assert r.outcome is CallOutcome.COMPLETED and run.ended_by == "end_call"
    assert r.urgency is Urgency.P3
    assert r.needs == [NeedReport(NeedCategory.HOME_DAMAGE, "roof leak", Urgency.P3)]
    assert r.summary.startswith("Spoke with Dorothy")
    assert r.transcript[0] == {"role": "agent", "line": voice_spec.render(voice_spec.FIRST_MESSAGE, name=h.name)}
    assert {t["role"] for t in r.transcript} == {"agent", "resident"}
    assert r.transcript[-1] == {"role": "agent", "line": voice_spec.CLOSING_LINE}
    assert 20 < r.duration_s < 120 and r.people == 1
    assert [c["name"] for c in run.tool_calls] == ["record_need", "end_call"]
    assert run.model_calls == len(fake.requests) and run.total_tokens == 110 * run.model_calls
    # The agent saw the stand-in tool result, and the shared spec's messages.
    voice_bodies = [b for b in fake.requests if b["model"] == settings.voice_model]
    assert voice_bodies[0]["messages"][:2] == voice_spec.start_messages(h.name)
    assert any(m["role"] == "tool" for m in voice_bodies[-1]["messages"])
    assert all(b.get("chat_template_kwargs") == {"enable_thinking": False} for b in fake.requests)

    # Rerunning the same call replays from the cache: same result, no new requests.
    sent = len(fake.requests)
    again = await simulate_call(llm, h, truth(urgency=Urgency.P3, needs=["home_damage:roof leak"]))
    await llm.aclose()
    assert len(fake.requests) == sent and again.cached_calls == again.model_calls
    assert again.result == r


async def test_downplaying_resident_flagged_p2(settings):
    def resident(body, turn):
        assert "I'm fine, honey" in body["messages"][0]["content"]
        if turn == 1:
            return "Oh, I'm fine, honey. That's just my oxygen machine beeping, don't mind it."
        return "Well, the power's out, so it's on the battery. Couple hours maybe."

    def agent(body, turn):
        if body["messages"][-1]["role"] == "tool":
            return "Our team is arranging help and will follow up soon."
        if turn == 1:
            return "I hear the machine. Is the power out, and how long will the battery last?"
        if turn == 2:
            return "", [tool("record_need", category="power", details="oxygen on battery, lives alone", urgency="P2"),
                        tool("flag_urgent", level="P2", reason="oxygen on battery")]
        return "", [tool("end_call", summary="Dorothy, oxygen on battery, P2.")]

    llm, _ = make_llm(settings, agent, resident)
    run = await simulate_call(llm, household(medical_device="oxygen concentrator"),
                              truth(urgency=Urgency.P2, needs=["power:oxygen concentrator on battery"],
                                    persona=Persona.DOWNPLAYS))
    await llm.aclose()
    assert run.result.outcome is CallOutcome.COMPLETED
    assert run.result.urgency is Urgency.P2
    assert run.result.needs[0].category is NeedCategory.POWER


async def test_hang_up_persona(settings):
    def resident(body, turn):
        return "Who is this?" if turn == 1 else f"I don't have time for this. {HANGUP_MARKER}"

    llm, fake = make_llm(settings, lambda b, t: "I'm checking on you after the tornado. Is anyone hurt?", resident)
    run = await simulate_call(llm, household(hid=2), truth(hid=2, persona=Persona.HANGS_UP))
    await llm.aclose()
    r = run.result
    assert r.outcome is CallOutcome.HUNG_UP and run.ended_by == "hang_up"
    assert r.urgency is None  # never marked safe
    assert r.transcript[-1] == {"role": "resident", "line": "I don't have time for this."}
    assert fake.count("resident") == 2 and "hung up" in r.summary


async def test_hang_up_is_enforced_even_without_the_marker(settings):
    llm, fake = make_llm(settings, lambda b, t: "Is anyone hurt?", lambda b, t: "No.")
    h = household(hid=3)  # odd id: hangs up on the 3rd reply
    run = await simulate_call(llm, h, truth(hid=3, persona=Persona.HANGS_UP))
    await llm.aclose()
    assert run.result.outcome is CallOutcome.HUNG_UP and fake.count("resident") == 3


async def test_never_answers_makes_no_model_calls(settings):
    def boom(body, turn):
        raise AssertionError("no model call expected")

    llm, fake = make_llm(settings, boom, boom)
    for t in (truth(answers=None), truth(persona=Persona.NEVER_ANSWERS)):
        run = await simulate_call(llm, household(), t)
        assert run.result.outcome is CallOutcome.NO_ANSWER and run.result.urgency is None
        assert run.model_calls == 0
    await llm.aclose()
    assert fake.requests == []


async def test_transfer_ends_the_call(settings):
    def agent(body, turn):
        if turn == 1:
            return "Please hang up and call 911. I'm connecting you to a person now.", [
                tool("record_need", category="home_damage", details="smells gas", urgency="P1"),
                tool("flag_urgent", level="P1", reason="smells gas"),
                tool("transfer_to_human", reason="gas smell"),
            ]
        raise AssertionError("the call should have ended")

    llm, _ = make_llm(settings, agent, lambda b, t: "I smell gas in the kitchen!")
    run = await simulate_call(llm, household(), truth(urgency=Urgency.P1, needs=["home_damage:smells gas inside the house"]))
    await llm.aclose()
    r = run.result
    assert r.outcome is CallOutcome.TRANSFERRED and r.urgency is Urgency.P1
    assert r.transcript[-1]["line"].startswith("Please hang up and call 911")
    assert "Transferred" in r.summary


async def test_turn_cap_ends_a_runaway_call(settings):
    llm, fake = make_llm(settings, lambda b, t: f"Question number {t}?", lambda b, t: "Hmm, what?")
    run = await simulate_call(llm, household(), truth(persona=Persona.CONFUSED), max_agent_turns=4)
    await llm.aclose()
    assert run.ended_by == "turn_cap" and run.result.outcome is CallOutcome.FAILED and run.result.urgency is None  # learned nothing: not reached
    assert fake.count("voice") == 4 and fake.count("resident") == 4
    assert sum(t["role"] == "agent" for t in run.result.transcript) == 5  # opening + 4
    assert "turn limit" in run.result.summary


async def test_agent_goodbye_without_end_call_ends_the_call(settings):
    llm, _ = make_llm(settings, lambda b, t: "Okay, stay safe. Goodbye.", chatty_resident)
    run = await simulate_call(llm, household(), truth())
    await llm.aclose()
    assert run.ended_by == "goodbye" and run.result.urgency is Urgency.P4


# --- store and batch ---------------------------------------------------------------------------


def stored_run(hid=7):
    result = CallResult(
        household_id=hid, attempt=1, channel=Channel.SIM, outcome=CallOutcome.COMPLETED, urgency=Urgency.P2,
        needs=[NeedReport(NeedCategory.POWER, "oxygen on battery", Urgency.P2)], summary="P2 oxygen.",
        transcript=[{"role": "agent", "line": "Hi"}, {"role": "resident", "line": "Hola, señor"}],
        duration_s=95.5, people=2,
    )
    return StoredOutcome(result=result, tool_calls=[{"name": "flag_urgent", "arguments": {"level": "P2"}, "turn": 1}],
                         prompt_tokens=900, completion_tokens=80, model_calls=9, cached_calls=0, elapsed_s=4.2,
                         ended_by="end_call", models={"voice": "v"})


def test_store_round_trips_a_call_result(settings):
    store = OutcomeStore.open(settings)
    assert store.path == store_path(settings) == settings.data_dir / "cache" / "sim_outcomes.db"
    key = SimKey.current(42, settings)
    assert key.spec_hash == voice_spec.SPEC_HASH and key.voice_model == settings.voice_model
    store.save(key, stored_run(7))
    store.save(key, stored_run(8))
    loaded = store.load(key, 7)
    assert loaded.result == stored_run(7).result
    assert loaded.tool_calls == stored_run(7).tool_calls and loaded.total_tokens == 980
    assert loaded.ended_by == "end_call" and loaded.models == {"voice": "v"}
    assert store.count(key) == 2 and set(store.load_all(key)) == {7, 8}
    assert store.load(key, 9) is None
    assert store.count(SimKey(43, key.spec_hash, key.voice_model, key.resident_model)) == 0  # other seed
    store.close()


def test_spec_hash_change_invalidates_stored_results(settings, monkeypatch):
    store = OutcomeStore.open(settings)
    old = SimKey.current(42, settings)
    store.save(old, stored_run(7))
    monkeypatch.setattr(voice_spec, "SPEC_HASH", "different123")
    new = SimKey.current(42, settings)
    assert new != old
    assert store.load(new, 7) is None and store.count(new) == 0 and store.stored_ids(new) == set()
    assert store.load(old, 7) is not None  # the old row is kept, just not counted
    store.close()


def small_city():
    people = [household(1, "Alice Adams"), household(2, "Bob Brown"), household(3, "Cara Cruz"),
              household(4, "Dan Diaz")]
    truths = [truth(1, Urgency.P3, ["home_damage:roof leak"]), truth(2), truth(3, Urgency.P2, ["power:no power"]),
              truth(4, persona=Persona.NEVER_ANSWERS, answers=None)]
    return City(42, {h.id: h for h in people}, {t.household_id: t for t in truths})


async def test_batch_resumes_skipping_stored_households(settings):
    llm, fake = make_llm(settings, normal_agent, chatty_resident)
    store = OutcomeStore.open(settings)
    key = SimKey.current(42, settings)
    city = small_city()
    store.save(key, stored_run(2))  # stored by an earlier, crashed run

    seen = []
    progress = await run_batch(llm, store, key, city, [1, 2, 3, 4], concurrency=2, on_progress=seen.append)
    assert (progress.skipped, progress.done, progress.failed) == (1, 3, 0)
    names = " ".join(b["messages"][0]["content"] for b in fake.requests if b["model"] == settings.resident_model)
    assert "Alice Adams" in names and "Cara Cruz" in names
    assert "Bob Brown" not in names and "Dan Diaz" not in names
    assert store.count(key) == 4 and store.load(key, 4).result.outcome is CallOutcome.NO_ANSWER
    assert store.load(key, 2).result.summary == "P2 oxygen."  # untouched
    assert seen and progress.tokens > 0 and "4/4 stored" in progress.line()

    # Running again does nothing: everything is stored.
    sent = len(fake.requests)
    again = await run_batch(llm, store, key, city, [1, 2, 3, 4])
    assert (again.skipped, again.done) == (4, 0) and len(fake.requests) == sent

    text = summarize(city, store.load_all(key), [1, 2, 3, 4])
    assert "Stored calls: 4 of 4" in text and "Urgent found" in text and "Tokens per" in text
    await llm.aclose()
    store.close()


async def test_batch_retries_a_failed_call_then_moves_on(settings):
    def broken(body, turn):
        raise httpx.ConnectError("down")

    llm, _ = make_llm(settings, broken, broken)
    store = OutcomeStore.open(settings)
    key = SimKey.current(42, settings)
    progress = await run_batch(llm, store, key, small_city(), [1, 4], backoff=(0,))
    assert (progress.done, progress.failed) == (1, 1)  # 4 never answers; 1 failed and isn't stored
    assert store.stored_ids(key) == {4}
    await llm.aclose()
    store.close()


def test_select_households_tiers():
    city = small_city()
    city.truths[2].persona_style = Persona.ANGRY
    assert select_households(city, "traps") == [2]
    all_ids = select_households(city, "all")
    assert all_ids[0] == 3 and set(all_ids) == {1, 2, 3, 4}  # true P2 first
    pilot = select_households(city, "pilot", pilot_n=2)
    assert pilot[0] == 3 and len(pilot) == 2
    with pytest.raises(ValueError):
        select_households(city, "everything")
    assert batch.TRAP_PERSONAS.isdisjoint({Persona.NORMAL, Persona.NEVER_ANSWERS})
