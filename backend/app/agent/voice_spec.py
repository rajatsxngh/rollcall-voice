"""Shared voice-agent spec v1: the prompt, opening line and tool schemas for every check-in call.

Design decision 7 (MILESTONES.md): one spec for every call channel. Vapi uses it for real calls
(scripts/vapi_setup.py puts it on the assistant; /vapi/chat/completions runs the voice model on it)
and the simulator (M7) runs the same spec over text, so "the test used the same agent" stays true.

Everything here is plain data: strings and OpenAI-format tool schemas, no I/O. Vapi fills
{{name}} from the call's variableValues; other channels use render(text, name=...) or
start_messages(name). The backend handlers for the tools live in app.agent.voice_tools.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.models import NeedCategory, Urgency

VERSION = "v1"
TARGET_SECONDS = 120  # the PRD's "under 2 minutes"

# The PRD's opening line. Vapi speaks it before the model's first turn.
FIRST_MESSAGE = (
    "Hi, this is RollCall, an automated assistant calling for State Farm about the tornado warning"
    " near you. Is this {{name}}? Are you safe right now?"
)

# The PRD's check-in questions, in order. The agent skips anything already answered.
QUESTIONS: tuple[str, ...] = (
    "Is anyone hurt or in danger right now?",
    "Do you have power? Does anyone rely on a medical device, or medicine that needs power or a fridge?",
    "Is your home damaged? Can you stay there tonight?",
    "Do you need help leaving, supplies, or a place to stay?",
    "Who is with you, including kids or pets?",
)

URGENCY_LEVELS: dict[Urgency, str] = {
    Urgency.P1: "Emergency, life in danger now: injury, someone trapped, gas smell, fire,"
    " a medical device that has died.",
    Urgency.P2: "Urgent, needs help within hours: oxygen or CPAP on battery, an older person alone"
    " with no power, a home that can't be lived in.",
    Urgency.P3: "Needs help within a day: roof leak, tree down, needs supplies.",
    Urgency.P4: "Safe: no needs.",
}

SAFETY_RULES: tuple[str, ...] = (
    "You are an automated assistant. Say so if asked, and transfer them to a person whenever they"
    " ask for one.",
    "Never give medical advice, even if asked.",
    'For any danger, say "Please hang up and call 911," flag it P1, then transfer them to a person.',
    "Never accept \"I'm fine\" at face value: before treating anyone as safe, ask about medical"
    " devices that need power and who is home with them.",
    "Catch downplaying: \"I'm fine, honey, my oxygen machine just keeps beeping\" is P2 or P1,"
    " not safe.",
    "If they speak Spanish, talk with them in Spanish. If after two tries you still can't hear or"
    " understand them, or they seem confused, never treat them as safe: tell them a person will"
    " call them back, then call transfer_to_human.",
    "Call record_need in the same turn you learn of a need, before you say anything more about it."
    " Saying a need out loud does not save it, and they may hang up at any moment.",
    "A medical device that has stopped because there is no power (CPAP, oxygen) is P1. One on"
    " battery, medicine that needs a fridge with the power out, or an older person alone with no"
    " power or cooling is P2.",
    "Only promise help that a tool result confirmed. Never make up places, addresses, distances or"
    " arrival times, such as a hospital or shelter by name.",
)

CLOSING_LINE = "Stay safe. Goodbye."  # Vapi hangs up when the agent says "goodbye"

# Spoken when the model fails or returns nothing, so the caller never sits in silence.
FALLBACK_REPLY = "Sorry, I didn't catch that. Could you say it again?"

TEMPERATURE = 0.3
MAX_TOKENS = 300  # per turn: one or two sentences plus a tool call or two


def _bullets(items: Any) -> str:
    return "\n".join(f"- {item}" for item in items)


SYSTEM_PROMPT = "\n\n".join(
    [
        "You are RollCall, an automated assistant calling for State Farm. A tornado warning covers"
        " the area around {{name}}'s home, and you are on a live phone call to check on them. Find out"
        " quickly whether they are safe, what they need and how urgent it is, save it with your tools,"
        " and end with a clear next step. Keep the whole call under 2 minutes.",
        f'You already opened the call with: "{FIRST_MESSAGE}" Do not introduce yourself again.',
        "HOW TO TALK\n"
        + _bullets(
            [
                "This is a phone call and your words are spoken aloud. Reply in one or two short"
                " sentences, then stop and listen.",
                "Ask one question at a time, in plain, calm, kind words.",
                'Never say lists, markdown, emojis, codes like "P2", or tool names.',
                "If it isn't {{name}} on the line, ask whether {{name}} is safe and whether anyone"
                " there needs help, then carry on with whoever answered.",
            ]
        ),
        "ASK THESE IN ORDER, skipping anything already answered:\n"
        + "\n".join(f"{i}. {question}" for i, question in enumerate(QUESTIONS, 1)),
        "URGENCY LEVELS\n" + _bullets(f"{level.value} {text}" for level, text in URGENCY_LEVELS.items()),
        "TOOLS\n"
        + _bullets(
            [
                "record_need: as soon as you learn of a need, once per need, with its category,"
                " short details and urgency.",
                "flag_urgent: right away for anything P1 or P2.",
                "transfer_to_human: whenever they ask for a person, and for any danger after"
                " telling them to call 911.",
                "send_text: to text them the next steps.",
                "end_call: once, when you are done, with a one-sentence summary for the response team.",
            ]
        ),
        "SAFETY RULES (always)\n" + _bullets(SAFETY_RULES),
        "ENDING\nTell them the next step in one sentence, call end_call, then say"
        f' "{CLOSING_LINE}"',
    ]
)


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# The PRD's five in-call tools, in OpenAI function-calling format.
TOOLS: list[dict[str, Any]] = [
    _tool(
        "record_need",
        "Save one need this household has. Call it as soon as you learn of a need, once per need.",
        {
            "category": {
                "type": "string",
                "enum": [c.value for c in NeedCategory],
                "description": "The kind of need. Use contact for requests like 'call my daughter instead'.",
            },
            "details": {
                "type": "string",
                "description": "A few plain words on what is wrong and who it affects,"
                " e.g. 'oxygen machine on battery, lives alone'.",
            },
            "urgency": {
                "type": "string",
                "enum": [u.value for u in Urgency],
                "description": "P1 emergency (life in danger now), P2 urgent (help within hours),"
                " P3 needs help (within a day), P4 safe.",
            },
        },
        ["category", "details", "urgency"],
    ),
    _tool(
        "flag_urgent",
        "Raise this household to P1 or P2 right away, so help goes to them first.",
        {
            "level": {
                "type": "string",
                "enum": [Urgency.P1.value, Urgency.P2.value],
                "description": "P1 emergency (life in danger now) or P2 urgent (needs help within hours).",
            },
            "reason": {"type": "string", "description": "Why, in a few plain words, e.g. 'smells gas'."},
        },
        ["level", "reason"],
    ),
    _tool(
        "transfer_to_human",
        "Hand the call to a human dispatcher. Use it whenever the caller asks for a person, and for"
        " any danger after telling them to call 911.",
        {"reason": {"type": "string", "description": "Why, in a few plain words."}},
        ["reason"],
    ),
    _tool(
        "send_text",
        "Send the caller a short text message, such as a confirmation or their next steps.",
        {"message": {"type": "string", "description": "The text to send, under 300 characters."}},
        ["message"],
    ),
    _tool(
        "end_call",
        "End the call and pass a summary to the response team. Call it once, after telling the"
        " caller the next step.",
        {
            "summary": {
                "type": "string",
                "description": "One sentence: who you spoke to, their needs and urgency, and what"
                " you told them happens next.",
            }
        },
        ["summary"],
    ),
]
TOOL_NAMES: tuple[str, ...] = tuple(t["function"]["name"] for t in TOOLS)

# Changes whenever the prompt, opening line or tools change; M7 keys its cached simulations on it.
SPEC_HASH = hashlib.sha256(
    json.dumps({"first": FIRST_MESSAGE, "prompt": SYSTEM_PROMPT, "tools": TOOLS}, sort_keys=True).encode()
).hexdigest()[:12]

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def render(text: str, **variables: Any) -> str:
    """Fill {{name}}-style variables the way Vapi does; unknown variables become empty strings."""
    return _VARIABLE.sub(lambda m: str(variables.get(m.group(1), "")), text)


def start_messages(name: str) -> list[dict[str, str]]:
    """The conversation as the model first sees it: the system prompt, then the opening line the
    platform already spoke. A text channel appends the resident's reply and asks the model."""
    return [
        {"role": "system", "content": render(SYSTEM_PROMPT, name=name)},
        {"role": "assistant", "content": render(FIRST_MESSAGE, name=name)},
    ]
