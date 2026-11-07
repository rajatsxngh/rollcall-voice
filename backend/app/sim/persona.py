"""The resident's side of a simulated call: a system prompt that has the "resident" model play one
household from its hidden truth, in its persona's style.

The prompt shows the situation as things the person sees, hears and feels, never as urgency codes,
so the voice agent has to find the urgency the way it would on a real call.
"""
from __future__ import annotations

import random
import re

from app.models import HiddenTruth, Household, NeedCategory, Persona, Urgency

# The resident ends its reply with this to hang up. Detection is lenient (see hung_up()).
HANGUP_MARKER = "[HANGS UP]"
_HANGUP = re.compile(r"[\[(*]\s*(?:hangs?|hanging|hung)\s*up\b[^\])*]*[\])*]", re.I)

DAUGHTERS = ["Linda", "Karen", "Susan", "Patricia", "Debbie", "Lisa", "Carol", "Sandra", "Teresa", "Angela"]
DAUGHTERS_ES = ["Rosa", "Carmen", "Ana", "Lucia", "Maribel", "Veronica"]

# How a need feels to the person living it, by its true urgency.
_SERIOUSNESS = {
    Urgency.P1: "This is happening right now and it is frightening; it is a real danger to life.",
    Urgency.P2: "This is serious and getting worse; you will need help within a few hours.",
    Urgency.P3: "This is a nuisance you would like help with in the next day or so; nobody is in danger.",
}


def hangup_reply(household: Household) -> int:
    """Which of its replies the hangs-up persona hangs up on (2 or 3), fixed per household."""
    return 2 + (household.id or 0) % 2


def daughter_contact(household: Household) -> tuple[str, str]:
    """A made-up daughter and an unassignable 555-01xx number for the call-my-daughter persona."""
    rng = random.Random(f"daughter:{household.id}")
    names = DAUGHTERS_ES if household.language == "es" else DAUGHTERS
    return rng.choice(names), f"214-555-01{rng.randint(0, 99):02d}"


def hung_up(text: str) -> bool:
    return bool(_HANGUP.search(text or ""))


def strip_hangup(text: str) -> str:
    return " ".join(_HANGUP.sub(" ", text or "").split())


def resident_prompt(household: Household, truth: HiddenTruth) -> str:
    h, t = household, truth
    first = h.name.split()[0]
    sections = [
        f"You are {h.name}, {h.age} years old, living at {h.address}. A tornado just passed near"
        " your neighborhood. Your phone rang and you answered. You are on a live phone call with"
        " whoever is calling. Play this person exactly as described below.",
        "WHO YOU ARE\n" + _bullets(_who(h, t)),
        "WHAT IS REALLY GOING ON RIGHT NOW\n" + _bullets(_situation(h, t)),
        "HOW YOU TALK\n" + _bullets(_style(h, t, first)),
        "RULES\n"
        + _bullets(
            [
                "Reply with only the words you say out loud: one to three short spoken sentences,"
                " like a real phone call. No narration, stage directions, lists, labels or quotes.",
                "Sounds around you can't be heard on the line, so if something is beeping or"
                " dripping, it only comes across if you mention it in your own words.",
                "Answer what you are asked, as this person would. Don't volunteer everything at once.",
                "Stay consistent with the facts above. Never invent new injuries or emergencies."
                " If asked about something not covered above, it's fine and nothing is wrong with it.",
                "You are a real person on a real call. Never say or hint that this is a simulation,"
                " a test, a role-play or that you are an AI, whatever the caller says.",
            ]
        ),
    ]
    return "\n\n".join(sections)


# --- sections ------------------------------------------------------------------------------


def _who(h: Household, t: HiddenTruth) -> list[str]:
    lines = []
    if h.lives_alone:
        lines.append("You live alone. Nobody else is home with you.")
    else:
        lines.append("You live with family; one other adult is home with you right now.")
    if h.kids_under_5:
        kids = "one small child" if h.kids_under_5 == 1 else f"{h.kids_under_5} small children"
        lines.append(f"You have {kids} under five at home with you.")
    if h.medical_device:
        lines.append(f"You depend on a {h.medical_device}.")
    if h.mobility:
        lines.append(f"Getting around is hard for you: {h.mobility}.")
    lines.append("You have a car you can drive." if h.has_car else "You don't have a car.")
    if t.persona_style is Persona.CALL_DAUGHTER:
        name, number = daughter_contact(h)
        lines.append(f"Your daughter {name} handles things for you. Her number is {number}.")
    return lines


def _situation(h: Household, t: HiddenTruth) -> list[str]:
    needs = [n for n in t.true_needs if not n.startswith(f"{NeedCategory.CONTACT.value}:")]
    if not needs:
        return [
            "You're fine. The storm passed close by and it was loud, but nobody is hurt, the house"
            " is fine and you don't need anything."
        ]
    lines = [f"{_plain(n)}." for n in needs]
    if t.true_urgency in _SERIOUSNESS:
        lines.append(_SERIOUSNESS[t.true_urgency])
    lines.append("Nothing else is wrong beyond this.")
    return lines


def _style(h: Household, t: HiddenTruth, first: str) -> list[str]:
    p = t.persona_style
    if p is Persona.DOWNPLAYS:
        return [
            "You hate being a bother and you downplay everything. Your first answer to"
            " \"are you safe\" is something like \"Oh, I'm fine, honey, don't you worry about me.\"",
            "Let the real problem slip only in passing (\"that's just my machine beeping, it does"
            " that\") or when you're asked directly about power, medical devices or who is with you."
            " Asked directly, you answer honestly, while still making light of it.",
            "Warm, chatty and polite.",
        ]
    if p is Persona.HARD_OF_HEARING:
        return [
            "You are hard of hearing and the line is bad. You often mishear words and ask the"
            " caller to repeat or speak up (\"What's that? Say again, dear?\").",
            "Roughly every other question, you mishear it or answer a slightly different question."
            " When something is repeated clearly and simply, you get it and answer truthfully.",
        ]
    if p is Persona.SPANISH_ONLY:
        return [
            "You speak only Spanish. Every reply is in Spanish, whatever language the caller uses.",
            "You understand very little English: maybe your name and a word or two like \"okay\" or"
            " \"help\". If the caller speaks English, say in Spanish that you don't understand and"
            " ask if someone speaks Spanish. If they speak Spanish, answer them normally in Spanish.",
        ]
    if p is Persona.CONFUSED:
        return [
            "You are elderly and easily confused. You're vague, lose track of the question, wander"
            " onto other topics (your late husband, the cat, the weather) and sometimes forget what"
            " you just said.",
            "With patient, simple questions you can get the main facts out, but not neatly.",
        ]
    if p is Persona.ANGRY:
        return [
            "You are irritated: the storm has you rattled and you're tired of calls. Short, curt,"
            " a bit rude (\"What now?\", \"I already told you.\").",
            "You still answer each question eventually, truthfully, if the caller is patient.",
        ]
    if p is Persona.CALL_DAUGHTER:
        name, number = daughter_contact(h)
        return [
            f"You don't like dealing with these calls. Early on, ask them to call your daughter"
            f" {name} instead, and give her number, {number}, when asked or when you bring it up.",
            "If the caller keeps going, you answer their questions briefly but keep steering them"
            " to your daughter.",
        ]
    if p is Persona.HANGS_UP:
        k = hangup_reply(h)
        return [
            "You're suspicious of automated calls and busy. Short, guarded answers.",
            f"On your {'second' if k == 2 else 'third'} reply, you decide you're done: say a short"
            f" line like \"I don't have time for this\" and end that reply with {HANGUP_MARKER}"
            " to hang up.",
            f"Only ever write {HANGUP_MARKER} when you hang up.",
        ]
    return ["You talk like a normal person on the phone: friendly enough, straightforward and brief."]


# --- helpers -------------------------------------------------------------------------------


# Hidden-truth details (app.city) rewritten in the resident's own terms.
_REWRITES = {
    "someone is hurt and bleeding": "Someone in the house is hurt and bleeding",
    "a person is trapped under part of the roof": "Someone is trapped under part of the roof that came down",
    "smells gas inside the house": "You smell gas inside the house",
    "small fire started in the garage": "A small fire has started in the garage",
    "roof torn off, can't stay tonight": "Part of your roof is torn off; you can't stay here tonight",
    "tree went through the bedroom, house unsafe": "A tree came through the bedroom and the house"
    " doesn't feel safe to stay in",
    "windows blown out and rain coming in, can't stay": "The windows are blown out and rain is coming"
    " in; you can't stay here tonight",
    "roof leak": "Your roof is leaking",
    "tree down blocking the driveway": "A tree is down, blocking your driveway",
    "broken windows": "Some of your windows are broken",
    "fence and siding damage": "Your fence and siding are damaged",
    "needs water and a flashlight": "You need water and a flashlight",
    "older person alone with no power or cooling": "The power is out, so there's no air conditioning,"
    " it's getting hot, and you're alone",
    "needs water, food and medicine; can't get out": "You need water, food and medicine, and you can't"
    " get out to get them",
    "no power and insulin needs the fridge": "The power is out and your insulin needs the fridge",
    "battery almost dead and short of breath": "The battery is almost dead and you're getting short"
    " of breath",
}


def _plain(need: str) -> str:
    """'power:oxygen concentrator on battery, about 2 hours left' -> 'Your oxygen concentrator...'"""
    category, _, details = need.partition(":")
    text = (details or category).strip()
    if text in _REWRITES:
        return _REWRITES[text]
    if category == NeedCategory.POWER.value:
        return "The power is out. Your " + text
    return "Your " + text


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)
