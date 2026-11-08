"""One simulated check-in call: the shared voice spec (job "voice") talks over text with a resident
played from its hidden truth (job "resident"), until end_call, transfer_to_human, a hang-up or the
turn cap.

Tool calls get stand-in results here and are recorded; the engine acts on them later, at replay
(M9). Every model call goes through llm.chat(..., cache=True, thinking=False), so a rerun of the
same household, spec and models replays from the cache for free and gives the same conversation.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent import voice_spec
from app.agent.voice_tools import parse_arguments, parse_category, parse_urgency
from app.engine.channels import CallResult, NeedReport
from app.llm import LLMClient, LLMResult
from app.models import CallOutcome, Channel, HiddenTruth, Household, Persona, Urgency
from app.sim.persona import hangup_reply, hung_up, resident_prompt, strip_hangup

MAX_AGENT_TURNS = 10  # spoken agent replies after the opening line
MAX_TOOL_ROUNDS = 3  # tool-only model replies in a row before the agent must speak
RESIDENT_TEMPERATURE = 0.7
RESIDENT_MAX_TOKENS = 160
WORDS_PER_MINUTE = 150
SECONDS_PER_LINE = 1.5  # pauses, turn-taking and reply delay
RING_SECONDS = 8.0

STAND_IN = {
    "record_need": "Need saved. Go on to the next unanswered question.",
    "flag_urgent": "Flagged as urgent for the response team; help is being arranged. Tell them the"
    " team will follow up soon, then carry on.",
    "flag_urgent_p1": "Flagged as an emergency; help is being arranged. Make sure they've been told"
    " to hang up and call 911, then transfer them to a person.",
    "send_text": "Text sent.",
    "end_call": 'Summary saved. Now say "Stay safe. Goodbye."',
    "transfer_to_human": "Transferring the call to a person now.",
}
_GOODBYE = re.compile(r"\bgood\s*-?\s*bye\b", re.I)
_ACTION = re.compile(r"\*[^*]{1,60}\*")


@dataclass
class SimRun:
    """One simulated conversation and what it cost."""

    result: CallResult
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{"name", "arguments", "turn"}]
    prompt_tokens: int = 0  # every model call, including ones replayed from the cache
    completion_tokens: int = 0
    model_calls: int = 0
    cached_calls: int = 0
    elapsed_s: float = 0.0
    ended_by: str = ""  # end_call, transfer_to_human, hang_up, goodbye, turn_cap, no_answer
    models: dict[str, str] = field(default_factory=dict)  # job -> model that actually answered
    new_tokens: int = 0  # tokens actually spent now (not replayed from the cache); not stored

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def people_in(household: Household) -> int:
    """People in the home, from what the household record says (for shelter beds)."""
    return (1 if household.lives_alone else 2) + household.kids_under_5


def estimate_duration(transcript: list[dict[str, Any]]) -> float:
    words = sum(len(str(t.get("line", "")).split()) for t in transcript)
    return round(RING_SECONDS + words / WORDS_PER_MINUTE * 60 + SECONDS_PER_LINE * len(transcript), 1)


def no_answer_result(household: Household, attempt: int = 1) -> CallResult:
    return CallResult(
        household_id=household.id, attempt=attempt, channel=Channel.SIM,
        outcome=CallOutcome.NO_ANSWER, summary="No answer.", duration_s=30.0,
    )


def most_severe(levels: list[Urgency]) -> Urgency | None:
    return min(levels, default=None)  # "P1" < "P2" < ...


async def simulate_call(
    llm: LLMClient,
    household: Household,
    truth: HiddenTruth,
    *,
    max_agent_turns: int = MAX_AGENT_TURNS,
) -> SimRun:
    """Run one answered conversation (attempt 1). Never-answers personas make no model calls."""
    started = time.perf_counter()
    if truth.answers_on_attempt is None or truth.persona_style is Persona.NEVER_ANSWERS:
        return SimRun(result=no_answer_result(household), ended_by="no_answer")

    convo = _Conversation(llm, household, truth)
    run = await convo.play(max_agent_turns)
    run.elapsed_s = round(time.perf_counter() - started, 3)
    return run


class _Conversation:
    def __init__(self, llm: LLMClient, household: Household, truth: HiddenTruth) -> None:
        self.llm = llm
        self.h = household
        self.truth = truth
        opening = voice_spec.render(voice_spec.FIRST_MESSAGE, name=household.name)
        self.agent_msgs: list[dict[str, Any]] = voice_spec.start_messages(household.name)
        self.resident_msgs: list[dict[str, Any]] = [
            {"role": "system", "content": resident_prompt(household, truth)},
            {"role": "user", "content": opening},
        ]
        self.transcript: list[dict[str, Any]] = [{"role": "agent", "line": opening}]
        self.tool_calls: list[dict[str, Any]] = []
        self.needs: list[NeedReport] = []
        self.flags: list[Urgency] = []
        self.summary = ""
        self.run = SimRun(result=None)  # type: ignore[arg-type]  # filled in by play()

    async def play(self, max_agent_turns: int) -> SimRun:
        resident_replies = 0
        agent_turns = 0
        ended_by = "turn_cap"
        while True:
            # Resident speaks.
            reply = await self._resident()
            resident_replies += 1
            forced = self.truth.persona_style is Persona.HANGS_UP and resident_replies >= hangup_reply(self.h)
            line = _clean_resident(strip_hangup(reply), self.h.name)
            if line:
                self.transcript.append({"role": "resident", "line": line})
            if hung_up(reply) or forced:
                ended_by = "hang_up"
                break
            self.resident_msgs.append({"role": "assistant", "content": line or "..."})
            self.agent_msgs.append({"role": "user", "content": line or "..."})

            # Agent speaks (possibly after some tool calls).
            agent_turns += 1
            spoken, terminal = await self._agent_turn(agent_turns)
            if spoken:
                self.transcript.append({"role": "agent", "line": spoken})
            if terminal:
                ended_by = terminal
                break
            if _GOODBYE.search(spoken or ""):
                ended_by = "goodbye"  # the phone platform hangs up on "goodbye"
                break
            if agent_turns >= max_agent_turns:
                break  # ended_by stays "turn_cap"
            self.resident_msgs.append({"role": "user", "content": spoken})
        self.run.result = self._result(ended_by)
        self.run.tool_calls = self.tool_calls
        self.run.ended_by = ended_by
        return self.run

    # --- the two sides ---

    async def _resident(self) -> str:
        result = await self.llm.chat(
            "resident", self.resident_msgs, thinking=False, temperature=RESIDENT_TEMPERATURE,
            max_tokens=RESIDENT_MAX_TOKENS, cache=True,
        )
        self._count(result)
        return result.text

    async def _agent_turn(self, turn: int) -> tuple[str, str | None]:
        """One spoken agent reply. Returns (what it said, the terminal tool if the call ended)."""
        for _ in range(MAX_TOOL_ROUNDS + 1):
            result = await self.llm.chat(
                "voice", self.agent_msgs, thinking=False, tools=voice_spec.TOOLS,
                temperature=voice_spec.TEMPERATURE, max_tokens=voice_spec.MAX_TOKENS, cache=True,
            )
            self._count(result)
            text = " ".join((result.text or "").split())
            calls = _normalize_calls(result.tool_calls, len(self.tool_calls))
            if not calls:
                text = text or voice_spec.FALLBACK_REPLY
                self.agent_msgs.append({"role": "assistant", "content": text})
                return text, None
            self.agent_msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
            terminal = None
            for call in calls:
                name = call["function"]["name"]
                args = parse_arguments(call["function"]["arguments"])
                self.tool_calls.append({"name": name, "arguments": args, "turn": turn})
                outcome = self._apply(name, args)
                if name in ("end_call", "transfer_to_human") and terminal is None:
                    terminal = name
                self.agent_msgs.append({"role": "tool", "tool_call_id": call["id"], "content": outcome})
            if terminal == "end_call":
                return text or voice_spec.CLOSING_LINE, terminal
            if terminal:
                return text, terminal
            if text:
                return text, None
        text = voice_spec.FALLBACK_REPLY  # the agent only kept calling tools
        self.agent_msgs.append({"role": "assistant", "content": text})
        return text, None

    def _apply(self, name: str, args: dict[str, Any]) -> str:
        """Record one tool call and return its stand-in result."""
        if name == "record_need":
            details = " ".join(str(args.get("details") or "").split()).rstrip(" .") or "no details given"
            urgency = parse_urgency(args.get("urgency"))
            self.needs.append(NeedReport(parse_category(args.get("category")), details, urgency))
            return STAND_IN["record_need"]
        if name == "flag_urgent":
            level = parse_urgency(args.get("level"), default=Urgency.P2)
            level = level if level in (Urgency.P1, Urgency.P2) else Urgency.P2
            self.flags.append(level)
            return STAND_IN["flag_urgent_p1" if level is Urgency.P1 else "flag_urgent"]
        if name == "end_call":
            self.summary = " ".join(str(args.get("summary") or "").split())[:500]
            return STAND_IN["end_call"]
        if name in STAND_IN:
            return STAND_IN[name]
        return f"Unknown tool {name!r}; use only the tools you were given."

    def _count(self, result: LLMResult) -> None:
        self.run.model_calls += 1
        self.run.cached_calls += int(result.cached)
        self.run.prompt_tokens += result.usage.prompt_tokens
        self.run.completion_tokens += result.usage.completion_tokens
        if not result.cached:
            self.run.new_tokens += result.usage.total_tokens
        if result.model:
            self.run.models.setdefault(result.job, result.model)

    # --- the outcome ---

    def _result(self, ended_by: str) -> CallResult:
        outcome = {
            "hang_up": CallOutcome.HUNG_UP,
            "transfer_to_human": CallOutcome.TRANSFERRED,
        }.get(ended_by, CallOutcome.COMPLETED)
        found = most_severe([n.urgency for n in self.needs] + self.flags)
        if found is None and ended_by == "turn_cap":
            outcome = CallOutcome.FAILED  # learned nothing: not reached, so the follow-up ladder takes over
        elif found is None and outcome is CallOutcome.COMPLETED:
            found = Urgency.P4
        first = self.h.name.split()[0]
        summary = self.summary or _fallback_summary(ended_by, first, self.needs)
        return CallResult(
            household_id=self.h.id,
            attempt=1,
            channel=Channel.SIM,
            outcome=outcome,
            urgency=found,
            needs=self.needs,
            summary=summary,
            transcript=self.transcript,
            duration_s=estimate_duration(self.transcript),
            people=people_in(self.h),
        )


def _fallback_summary(ended_by: str, first: str, needs: list[NeedReport]) -> str:
    found = "; ".join(n.details for n in needs) or "no needs recorded"
    lead = {
        "hang_up": f"{first} hung up partway through the call",
        "transfer_to_human": f"Transferred {first}'s call to a person",
        "turn_cap": f"Call with {first} hit the turn limit without a summary",
        "goodbye": f"Call with {first} ended without a summary",
    }.get(ended_by, f"Call with {first} ended")
    return f"{lead}: {found}."


def _normalize_calls(raw: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
    """Tool calls in the OpenAI wire format, each with an id and string arguments."""
    calls = []
    for i, call in enumerate(raw or []):
        function = call.get("function") or {}
        name = function.get("name") or call.get("name")
        if not name:
            continue
        args = function.get("arguments", call.get("arguments", {}))
        calls.append({
            "id": call.get("id") or f"call_{offset + i + 1}",
            "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else _dumps(args)},
        })
    return calls


def _dumps(value: Any) -> str:
    return json.dumps(value or {})


def _clean_resident(text: str, name: str = "") -> str:
    """Only the spoken words: drop a speaker label, *actions* and wrapping quotes."""
    text = _ACTION.sub(" ", text or "").strip()
    labels = ["resident", "caller", *(re.escape(n) for n in {name, name.split()[0] if name else ""} if n)]
    text = re.sub(rf"^\s*(?:{'|'.join(labels)})\s*:\s*", "", text, count=1, flags=re.I)
    text = " ".join(text.split()).strip().strip('"').strip()
    return text
