"""Risk scoring and call order, in plain code (the PRD keeps this math out of the model)."""
from __future__ import annotations

from app.geo import WarningArea
from app.models import CallPolicy, Household

# Points per factor (the maximum total is 100). Tune here; nothing else hard-codes them.
WEIGHTS = {
    "powered_device": 30.0,
    "age_75_plus": 18.0,
    "age_65_74": 8.0,
    "lives_alone": 12.0,
    "mobility_limits": 12.0,
    "no_car": 8.0,
    "kids_under_5": 6.0,
    "language_needs": 6.0,
    "near_center": 8.0,  # scaled by closeness to the center of the warning area
}

FACTOR_LABELS = {
    "powered_device": "Medical device that needs power",
    "age_75_plus": "Age 75 or older",
    "age_65_74": "Age 65 to 74",
    "lives_alone": "Lives alone",
    "mobility_limits": "Mobility limits",
    "no_car": "No car",
    "kids_under_5": "Kids under 5",
    "language_needs": "Prefers a language other than English",
    "near_center": "Near the center of the warning",
}

# Waves are risk tiers for the plan and the story; calls still start continuously in order.
WAVE_THRESHOLDS = (40.0, 20.0)


def score_household(h: Household, area: WarningArea) -> tuple[float, dict[str, float]]:
    """Risk score (0 to 100) and the points behind it, so the dashboard can say why."""
    factors: dict[str, float] = {}
    if h.medical_device:
        factors["powered_device"] = WEIGHTS["powered_device"]
    if h.age >= 75:
        factors["age_75_plus"] = WEIGHTS["age_75_plus"]
    elif h.age >= 65:
        factors["age_65_74"] = WEIGHTS["age_65_74"]
    if h.lives_alone:
        factors["lives_alone"] = WEIGHTS["lives_alone"]
    if h.mobility:
        factors["mobility_limits"] = WEIGHTS["mobility_limits"]
    if not h.has_car:
        factors["no_car"] = WEIGHTS["no_car"]
    if h.kids_under_5:
        factors["kids_under_5"] = WEIGHTS["kids_under_5"]
    if h.language != "en":
        factors["language_needs"] = WEIGHTS["language_needs"]
    closeness = max(0.0, 1.0 - area.distance_to_center_km(h.lat, h.lng) / area.reach_km)
    near = round(WEIGHTS["near_center"] * closeness, 1)
    if near > 0:
        factors["near_center"] = near
    return round(sum(factors.values()), 1), factors


def apply_scores(households: list[Household], area: WarningArea) -> None:
    for h in households:
        h.risk_score, h.risk_factors = score_household(h, area)


def wave_for(score: float) -> int:
    high, medium = WAVE_THRESHOLDS
    return 1 if score >= high else 2 if score >= medium else 3


def call_order(households: list[Household], policy: CallPolicy | str) -> list[Household]:
    """Risk-ranked (RollCall): live demo homes first, then highest risk. List order: by id."""
    policy = CallPolicy(policy)
    if policy is CallPolicy.LIST_ORDER:
        return sorted(households, key=lambda h: h.id)
    return sorted(households, key=lambda h: (not h.live, -h.risk_score, h.id))
