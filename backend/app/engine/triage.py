"""Triage: from what a call found to the kinds of help a household needs.

A decision depends only on the household and the call, never on which crews are free (design
decision 5). Booking happens afterwards, in code, from live availability. That keeps decisions
cacheable and both test runs comparable.

RuleTriage is fixed rules. In M5, Nemotron makes these decisions and falls back to RuleTriage
whenever the model is unavailable or answers badly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.channels import CallResult
from app.models import ActionType, Household, NeedCategory, Urgency


@dataclass
class HelpRequest:
    kind: ActionType
    reason: str  # plain words, shown in the feed
    needs_approval: bool = False


@dataclass
class Decision:
    urgency: Urgency | None  # None: nobody was reached
    help: list[HelpRequest] = field(default_factory=list)
    reason: str = ""
    source: str = "rules"  # "rules", or "nemotron" from M5


class RuleTriage:
    """Urgency to actions, by fixed rules."""

    source = "rules"

    async def decide(self, h: Household, result: CallResult) -> Decision:
        return self.decide_now(h, result)

    def decide_now(self, h: Household, result: CallResult) -> Decision:
        if not result.reached:
            return Decision(None, reason=result.summary or "Nobody was reached.")
        urgency = result.urgency or Urgency.P4
        categories = {n.category for n in result.needs}
        detail = result.needs[0].details if result.needs else "needs a check"
        help: list[HelpRequest] = []

        if urgency is Urgency.P1:
            help.append(HelpRequest(ActionType.TRANSFER_TO_HUMAN, detail))
        elif urgency is Urgency.P2:
            help.append(HelpRequest(ActionType.DISPATCH_CREW, detail))
            if NeedCategory.SHELTER in categories:
                help.append(HelpRequest(ActionType.RESERVE_SHELTER_BED, _detail(result, NeedCategory.SHELTER)))
            if h.medical_device and categories & {NeedCategory.POWER, NeedCategory.MEDICAL}:
                # Spec decision: the crew goes now; the generator waits at the top of the approvals queue.
                help.append(HelpRequest(ActionType.DELIVER_GENERATOR, f"{h.medical_device} needs power",
                                        needs_approval=True))
        elif urgency is Urgency.P3:
            if categories & {NeedCategory.HOME_DAMAGE, NeedCategory.SHELTER}:
                damage = _detail(result, NeedCategory.HOME_DAMAGE) or _detail(result, NeedCategory.SHELTER)
                help.append(HelpRequest(ActionType.SCHEDULE_ADJUSTER, damage))
                help.append(HelpRequest(ActionType.OPEN_CLAIM, damage))
            if categories & {NeedCategory.SUPPLIES, NeedCategory.POWER, NeedCategory.MEDICAL} or not help:
                help.append(HelpRequest(ActionType.DISPATCH_CREW, detail))

        if NeedCategory.CONTACT in categories:
            help.append(HelpRequest(ActionType.SCHEDULE_FOLLOWUP, _detail(result, NeedCategory.CONTACT)))
        return Decision(urgency, help, reason=f"{urgency.value}: {detail}" if result.needs else "Safe, no needs.")


def _detail(result: CallResult, category: NeedCategory) -> str:
    return next((n.details for n in result.needs if n.category is category), "")
