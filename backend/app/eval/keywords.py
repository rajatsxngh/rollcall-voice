"""The keyword baseline: what simple word-matching would decide from the same transcripts.

It's the "why AI and not a script?" comparison: keywords hear "I'm fine" and stop there; they miss
"my oxygen machine keeps beeping" said in passing, Spanish, and confused answers.
"""
from __future__ import annotations

import re
from typing import Any

from app.models import Urgency

P1_WORDS = r"\b(hurt|bleeding|trapped|can't breathe|cannot breathe|gas|fire|smoke|unconscious|chest pain)\b"
P2_WORDS = r"\b(no power|power(?:'s| is)? out|oxygen|cpap|dialysis|battery|can't stay|cannot stay|roof (?:is )?gone|collapsed)\b"
P3_WORDS = r"\b(leak|tree|window|damage|supplies|water|food|medicine|help)\b"
SAFE_WORDS = r"\b(fine|okay|ok|safe|all good|we're good|no problem)\b"


def keyword_urgency(transcript: list[dict[str, Any]]) -> Urgency:
    """Urgency from the resident's words alone, the way a keyword script would judge it."""
    said = " ".join(t.get("line", "") for t in transcript if t.get("role") == "resident").lower()
    if not said.strip():
        return Urgency.P4
    if re.search(P1_WORDS, said):
        return Urgency.P1
    safe_first = re.search(SAFE_WORDS, said[:120])  # the first thing they said sets the tone for a script
    if re.search(P2_WORDS, said) and not safe_first:
        return Urgency.P2
    if re.search(P3_WORDS, said) and not safe_first:
        return Urgency.P3
    return Urgency.P4
