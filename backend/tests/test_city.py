"""M2: the fake city, risk scoring and call order."""
import pytest

from app.city import build_city
from app.config import REPO_ROOT
from app.geo import WarningArea, find_affected
from app.models import CallPolicy, Household, Persona, Urgency
from app.risk import WEIGHTS, call_order, score_household, wave_for

AREA = WarningArea.load(REPO_ROOT / "data" / "warning_area.geojson")


@pytest.fixture(scope="module")
def city():
    return build_city(AREA, seed=42, n=2000)


def test_same_seed_same_city():
    a, b = build_city(AREA, seed=7, n=300), build_city(AREA, seed=7, n=300)
    dump = lambda c: [(h.name, h.address, h.lat, h.lng, h.age, h.risk_score) for h in c.households]  # noqa: E731
    assert dump(a) == dump(b)
    assert [(t.true_urgency, t.true_needs, t.persona_style) for t in a.truths] == [
        (t.true_urgency, t.true_needs, t.persona_style) for t in b.truths
    ]


def test_urgency_mix_traps_and_follow_up_fields(city):
    n = len(city.truths)
    urgency = [t.true_urgency for t in city.truths]
    urgent = sum(u in (Urgency.P1, Urgency.P2) for u in urgency) / n
    p3 = urgency.count(Urgency.P3) / n
    assert 0.03 <= urgent <= 0.08
    assert 0.14 <= p3 <= 0.26

    traps = [t for t in city.truths if t.persona_style not in (Persona.NORMAL, Persona.NEVER_ANSWERS)]
    assert len(traps) == 30
    by_id = {h.id: h for h in city.households}
    assert all(AREA.contains(by_id[t.household_id].lat, by_id[t.household_id].lng) for t in traps)
    downplayers = [t for t in traps if t.persona_style is Persona.DOWNPLAYS]
    assert len(downplayers) == 5 and all(t.true_urgency in (Urgency.P1, Urgency.P2) for t in downplayers)
    spanish = [by_id[t.household_id] for t in traps if t.persona_style is Persona.SPANISH_ONLY]
    assert all(h.language == "es" for h in spanish)

    for t in city.truths:
        assert (t.persona_style is Persona.NEVER_ANSWERS) == (t.answers_on_attempt is None)


def test_phones_are_undialable_and_unique(city):
    phones = [h.phone for h in city.households]
    assert len(set(phones)) == len(phones)
    assert all(p.startswith("+1555") for p in phones)
    assert not any(h.live for h in city.households)


def test_demo_households_are_live_inside_and_have_no_truth():
    c = build_city(AREA, seed=42, n=200, demo=[{"name": "Maria", "phone": "+12145550100"}])
    demo = [h for h in c.households if h.live]
    assert len(demo) == 1 and AREA.contains(demo[0].lat, demo[0].lng)
    assert demo[0].id not in {t.household_id for t in c.truths}
    assert call_order(find_affected(c.households, AREA), CallPolicy.RISK_RANKED)[0].live


def test_risk_ranking_finds_urgent_homes_far_sooner_than_list_order(city):
    affected = find_affected(city.households, AREA)
    truth = {t.household_id: t for t in city.truths}
    urgent = {h.id for h in affected if truth[h.id].true_urgency in (Urgency.P1, Urgency.P2)}
    first = round(len(affected) * 0.2)
    ranked = {h.id for h in call_order(affected, CallPolicy.RISK_RANKED)[:first]}
    listed = {h.id for h in call_order(affected, CallPolicy.LIST_ORDER)[:first]}
    assert len(urgent & ranked) >= 2 * len(urgent & listed)
    assert len(urgent & ranked) < len(urgent)  # ranking helps, but the calls still matter


def test_score_breakdown():
    lat, lng = AREA.center
    h = Household(id=1, name="Test", phone="+15550000001", address="x", lat=lat, lng=lng, age=81,
                  lives_alone=True, medical_device="oxygen concentrator", has_car=False)
    score, factors = score_household(h, AREA)
    assert factors["powered_device"] == WEIGHTS["powered_device"]
    assert factors["age_75_plus"] == WEIGHTS["age_75_plus"] and "age_65_74" not in factors
    assert factors["near_center"] == pytest.approx(WEIGHTS["near_center"], abs=0.2)
    assert score == pytest.approx(sum(factors.values()))
    assert wave_for(score) == 1


def test_point_in_polygon():
    assert AREA.contains(*AREA.center)
    assert not AREA.contains(33.20, -96.40)  # well outside, toward McKinney
