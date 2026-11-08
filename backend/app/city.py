"""The fake city: households with hidden truths, resources, and demo households.

Deterministic: the same seed always builds the same city. Everything is made up. Phone numbers use
the unassigned 555 area code, so they can never be dialed; the only real numbers are demo
households read from the gitignored data/demo_households.yaml.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from shapely.geometry import Point

from app.config import Settings
from app.db import Database
from app.geo import WarningArea, haversine_km
from app.models import (
    HiddenTruth,
    Household,
    Persona,
    Resource,
    ResourceType,
    RunInfo,
    Urgency,
)
from app.risk import apply_scores

P1, P2, P3, P4 = Urgency.P1, Urgency.P2, Urgency.P3, Urgency.P4
NEAR_DEG = 0.025  # how far outside the warning area "near" households live (~2.5 km)

TOWNS = {"Richardson": (32.948, -96.730), "Plano": (33.020, -96.699), "Garland": (32.913, -96.639)}
STREETS = [
    "Oak", "Maple", "Cedar", "Elm", "Pecan", "Willow", "Magnolia", "Bluebonnet", "Sunset", "Meadow",
    "Ridgeview", "Hillcrest", "Briarwood", "Cottonwood", "Lakeview", "Juniper", "Mesquite", "Redbud",
    "Highland", "Fairview", "Glenwood", "Brookhaven", "Timberline", "Prairie", "Sycamore", "Aspen",
    "Live Oak", "Hackberry", "Primrose", "Wildflower", "Canyon", "Spring Creek", "Shady Grove", "Laurel",
]
SUFFIXES = ["St", "Dr", "Ln", "Ct", "Ave", "Way", "Trl", "Cir", "Pl"]

NAMES = {
    "en": {
        "older": ["Dorothy", "Harold", "Betty", "Walter", "Evelyn", "Frank", "Margaret", "George",
                  "Helen", "Ruth", "Donald", "Shirley", "Raymond", "Joan", "Eugene", "Doris", "Earl",
                  "Gloria", "Vernon", "Mildred"],
        "younger": ["James", "Ashley", "Michael", "Jessica", "David", "Emily", "Daniel", "Sarah",
                    "Kevin", "Megan", "Chris", "Brittany", "Tyler", "Lauren", "Marcus", "Keisha",
                    "Andre", "Grace", "Ethan", "Olivia", "Jordan", "Taylor", "Brandon", "Hannah"],
        "last": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson",
                 "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "White", "Harris", "Martin",
                 "Thompson", "Clark", "Lewis", "Walker", "Hall", "Young", "Allen", "King", "Wright",
                 "Scott", "Green", "Baker", "Adams", "Nelson", "Carter", "Mitchell", "Turner"],
    },
    "es": {
        "older": ["Maria", "Jose", "Rosa", "Juan", "Carmen", "Guadalupe", "Jesus", "Esperanza",
                  "Manuel", "Dolores", "Ramon", "Consuelo"],
        "younger": ["Luis", "Ana", "Carlos", "Sofia", "Diego", "Lucia", "Miguel", "Elena", "Mateo",
                    "Valeria", "Javier", "Daniela", "Alejandro", "Camila"],
        "last": ["Garcia", "Hernandez", "Martinez", "Lopez", "Gonzalez", "Rodriguez", "Perez",
                 "Sanchez", "Ramirez", "Torres", "Flores", "Rivera", "Gomez", "Diaz", "Reyes",
                 "Morales", "Cruz", "Ortiz"],
    },
    "vi": {
        "older": ["Lan", "Tuan", "Hoa", "Duc", "Mai", "Binh"],
        "younger": ["Linh", "Minh", "Thao", "Huy", "Khanh", "Trang", "Bao", "Ngoc"],
        "last": ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui"],
    },
    "in": {
        "older": ["Kamala", "Ramesh", "Lakshmi", "Suresh", "Savitri", "Mohan"],
        "younger": ["Priya", "Raj", "Anil", "Deepa", "Arjun", "Kavya", "Sanjay", "Meera", "Vikram",
                    "Anita"],
        "last": ["Patel", "Shah", "Reddy", "Sharma", "Iyer", "Rao", "Gupta", "Desai", "Mehta"],
    },
}
DEVICES = ["oxygen concentrator", "CPAP machine", "home dialysis machine", "powered wheelchair",
           "insulin that needs a fridge", "nebulizer"]
MOBILITY = ["wheelchair", "walker", "mostly bedbound", "cane, can't do stairs"]

P1_EVENTS = [
    "injury:someone is hurt and bleeding",
    "injury:a person is trapped under part of the roof",
    "home_damage:smells gas inside the house",
    "home_damage:small fire started in the garage",
]
UNLIVABLE = [
    "shelter:roof torn off, can't stay tonight",
    "shelter:tree went through the bedroom, house unsafe",
    "shelter:windows blown out and rain coming in, can't stay",
]
MINOR_DAMAGE = [
    "home_damage:roof leak",
    "home_damage:tree down blocking the driveway",
    "home_damage:broken windows",
    "home_damage:fence and siding damage",
    "supplies:needs water and a flashlight",
]

# The PRD's trap set: six styles, 30 personas in all.
TRAPS = [
    (Persona.DOWNPLAYS, 5),
    (Persona.HARD_OF_HEARING, 5),
    (Persona.SPANISH_ONLY, 5),
    (Persona.CONFUSED, 3),
    (Persona.ANGRY, 2),
    (Persona.CALL_DAUGHTER, 5),
    (Persona.HANGS_UP, 5),
]

# 20 crews (local plus mutual aid). The PRD's sample of 6 left urgent homes waiting 6 to 12 hours for
# about 80 urgent jobs, so this is tuned up, as the PRD's fake-data plan allows. Spread along the track.
CREWS = [(round(32.885 + 0.0075 * i + (0.012 if i % 2 else -0.012), 4),
          round(-96.785 + 0.0085 * i + (0.006 if i % 3 == 0 else -0.004), 4)) for i in range(20)]
SHELTERS = [("Richardson East Shelter", 32.962, -96.712, 120),
            ("Plano North Shelter", 33.031, -96.700, 80),
            ("Garland West Shelter", 32.918, -96.655, 150)]
ADJUSTERS = [(32.950, -96.735), (32.990, -96.680), (32.930, -96.720), (33.010, -96.645),
             (32.905, -96.760), (32.970, -96.705), (33.025, -96.630), (32.945, -96.680)]
GENERATORS = [(32.958, -96.725), (33.000, -96.655)]


@dataclass
class City:
    households: list[Household]
    truths: list[HiddenTruth]
    resources: list[Resource]
    seed: int


def base_db_path(settings: Settings) -> Path:
    return settings.data_dir / "base.db"


def load_demo_households(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def build_city(
    area: WarningArea, seed: int = 42, n: int = 2000, demo: list[dict[str, Any]] | None = None
) -> City:
    rng = random.Random(seed)
    hoods = _neighborhoods(rng, area)
    near_zone = area.geometry.buffer(NEAR_DEG)
    people = [_person(rng, rng.choices(hoods, weights=[h["weight"] for h in hoods])[0], near_zone)
              for _ in range(n)]

    # Customer list order: alphabetical, like a policy list. This is the "list order" baseline.
    people.sort(key=lambda p: (p["last"], p["first"], p["address"]))
    households, truths = [], []
    for i, p in enumerate(people, start=1):
        inside = area.contains(p["lat"], p["lng"])
        household = Household(
            id=i, name=f"{p['first']} {p['last']}", phone=f"+1555{i:07d}", address=p["address"],
            lat=p["lat"], lng=p["lng"], age=p["age"], lives_alone=p["lives_alone"],
            medical_device=p["medical_device"], mobility=p["mobility"], has_car=p["has_car"],
            kids_under_5=p["kids_under_5"], language=p["language"],
        )
        households.append(household)
        truths.append(_truth(rng, household, inside))

    _add_traps(rng, households, truths, area)

    for j, d in enumerate(demo or []):
        households.append(_demo_household(len(households) + 1, j, d, area))

    apply_scores(households, area)
    return City(households, truths, _resources(), seed)


def write_city(city: City, path: Path) -> None:
    """Write a fresh database file (any old one at this path is replaced)."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    db = Database(path)
    db.create_all()
    with db.session() as session:
        session.add_all(city.households)
        session.flush()
        session.add_all(city.truths)
        session.add_all(city.resources)
        session.add(RunInfo(seed=city.seed, notes=f"{len(city.households)} households"))
        session.commit()
    db.dispose()


def copy_database(source: Path, target: Path) -> None:
    """Copy with SQLite's backup API, which is safe even while the backend has the target open."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src, dst = sqlite3.connect(source), sqlite3.connect(target, timeout=30)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


# --- generation details ------------------------------------------------------------------


def _neighborhoods(rng: random.Random, area: WarningArea, inside: int = 48, near: int = 16) -> list[dict]:
    poly = area.geometry
    ring = poly.buffer(NEAR_DEG).difference(poly)
    hoods: list[dict] = []
    for zone, count in ((poly, inside), (ring, inside + near)):
        minx, miny, maxx, maxy = zone.bounds
        while len(hoods) < count:
            lng, lat = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
            if zone.contains(Point(lng, lat)):
                town = min(TOWNS, key=lambda t: haversine_km(lat, lng, *TOWNS[t]))
                streets = [f"{s} {rng.choice(SUFFIXES)}" for s in rng.sample(STREETS, 4)]
                hoods.append({"lat": lat, "lng": lng, "town": town, "streets": streets,
                              "weight": rng.uniform(0.6, 1.4)})
    return hoods


def _age(rng: random.Random) -> int:
    band = rng.choices([(18, 34), (35, 54), (55, 64), (65, 74), (75, 94)], weights=[25, 33, 17, 14, 11])[0]
    return rng.randint(*band)


def _name(rng: random.Random, group: str, age: int) -> tuple[str, str]:
    pool = NAMES[group]
    first = rng.choice(pool["older"] if age >= 60 and rng.random() < 0.8 else pool["younger"])
    return first, rng.choice(pool["last"])


def _person(rng: random.Random, hood: dict, near_zone) -> dict[str, Any]:
    group = rng.choices(["en", "es", "vi", "in"], weights=[60, 25, 6, 9])[0]
    age = _age(rng)
    first, last = _name(rng, group, age)
    language = {"es": "es" if rng.random() < 0.45 else "en",
                "vi": "vi" if rng.random() < 0.5 else "en"}.get(group, "en")
    old, senior = age >= 75, age >= 65
    while True:  # jitter around the neighborhood, staying within the near zone
        lat, lng = rng.gauss(hood["lat"], 0.004), rng.gauss(hood["lng"], 0.005)
        if near_zone.contains(Point(lng, lat)):
            break
    return {
        "first": first, "last": last, "age": age, "language": language, "lat": round(lat, 6),
        "lng": round(lng, 6),
        "address": f"{rng.randint(100, 4999)} {rng.choice(hood['streets'])}, {hood['town']}, TX",
        "lives_alone": rng.random() < (0.45 if old else 0.22 if senior else 0.12),
        "medical_device": rng.choice(DEVICES) if rng.random() < (0.25 if old else 0.10 if senior else 0.03) else None,
        "mobility": rng.choice(MOBILITY) if rng.random() < (0.30 if old else 0.10 if senior else 0.03) else None,
        "has_car": rng.random() > (0.30 if old else 0.08),
        "kids_under_5": rng.choice([1, 1, 2]) if 22 <= age <= 45 and rng.random() < 0.30 else 0,
    }


def _truth(rng: random.Random, h: Household, inside: bool) -> HiddenTruth:
    """What is really going on. Correlated with the risk factors, but not decided by them: storm
    damage lands at random, so a young family can be urgent too, and only the call finds out."""
    needs: list[tuple[Urgency, str]] = []
    power_out = rng.random() < (0.75 if inside else 0.20)
    if inside:
        roll = rng.random()
        if roll < 0.010:
            needs.append((P1, rng.choice(P1_EVENTS)))
        elif roll < 0.026:
            needs.append((P2, rng.choice(UNLIVABLE)))
        elif roll < 0.29:
            needs.append((P3, rng.choice(MINOR_DAMAGE)))
    elif rng.random() < 0.08:
        needs.append((P3, rng.choice(MINOR_DAMAGE)))
    if power_out:
        device = h.medical_device
        if device:
            roll = rng.random()
            if inside and roll < 0.12:
                needs.append((P1, f"medical:{device} has stopped and there is no backup power"))
            elif roll < (0.60 if inside else 0.10):
                needs.append((P2, f"power:{device} on battery, about {rng.randint(1, 4)} hours left"))
            elif roll < 0.87:
                needs.append((P3, f"power:{device} has backup power but needs a check"))
        elif inside and h.age >= 75 and h.lives_alone and rng.random() < 0.35:
            needs.append((P2, "power:older person alone with no power or cooling"))
        if not h.has_car and h.mobility and rng.random() < 0.5:
            needs.append((P3, "supplies:needs water, food and medicine; can't get out"))

    never = rng.random() < 0.04
    return HiddenTruth(
        household_id=h.id,
        true_urgency=min((u for u, _ in needs), default=P4),
        true_needs=[text for _, text in needs],
        persona_style=Persona.NEVER_ANSWERS if never else Persona.NORMAL,
        answers_on_attempt=None if never else rng.choices([1, 2, 3], weights=[82, 12, 6])[0],
        replies_to_text=rng.random() < (0.40 if never else 0.75),
    )


def _add_traps(rng: random.Random, households: list[Household], truths: list[HiddenTruth], area: WarningArea) -> None:
    pool = [i for i, h in enumerate(households)
            if area.contains(h.lat, h.lng) and truths[i].persona_style is Persona.NORMAL]
    rng.shuffle(pool)
    taken: set[int] = set()

    def pick(prefer) -> int:
        for i in pool:
            if i not in taken and prefer(households[i]):
                taken.add(i)
                return i
        i = next(i for i in pool if i not in taken)
        taken.add(i)
        return i

    for persona, count in TRAPS:
        for k in range(count):
            if persona is Persona.DOWNPLAYS:
                i = pick(lambda h: h.medical_device and h.age >= 70)
                h, t = households[i], truths[i]
                h.age, h.lives_alone = max(h.age, 76), True
                h.medical_device = h.medical_device or "oxygen concentrator"
                t.true_urgency = P1 if k < 2 else P2
                t.true_needs = [f"power:{h.medical_device} alarm keeps beeping, running on battery"]
                if k < 2:
                    t.true_needs.append("medical:battery almost dead and short of breath")
            elif persona is Persona.SPANISH_ONLY:
                i = pick(lambda h: h.language == "es")
                h, t = households[i], truths[i]
                if h.language != "es":
                    h.name = " ".join(_name(rng, "es", h.age))
                h.language = "es"
                if k < 2 and t.true_urgency in (P3, P4):
                    h.medical_device = "insulin that needs a fridge"
                    t.true_urgency = P2
                    t.true_needs = ["power:no power and insulin needs the fridge"]
            else:
                i = pick(lambda h: h.age >= 75)
                h, t = households[i], truths[i]
                if persona is Persona.HARD_OF_HEARING:
                    h.age = max(h.age, 80)
                elif persona is Persona.CONFUSED:
                    h.age = max(h.age, 78)
                elif persona is Persona.CALL_DAUGHTER:
                    h.age = max(h.age, 75)
                    t.true_needs = [*t.true_needs, "contact:wants us to call their daughter instead"]
            t.persona_style = persona
            t.answers_on_attempt, t.replies_to_text = 1, True


def _demo_household(household_id: int, index: int, d: dict[str, Any], area: WarningArea) -> Household:
    lat, lng = area.center
    return Household(
        id=household_id, name=str(d["name"]), phone=str(d["phone"]),
        address=d.get("address", "1412 Oak St, Richardson, TX"),
        lat=round(lat + 0.002 * index, 6), lng=round(lng - 0.002 * index, 6),
        age=int(d.get("age", 81)), lives_alone=bool(d.get("lives_alone", True)),
        medical_device=d.get("medical_device", "oxygen concentrator"), mobility=d.get("mobility"),
        has_car=bool(d.get("has_car", False)), kids_under_5=int(d.get("kids_under_5", 0)),
        language=d.get("language", "en"), live=True,
    )


def _resources() -> list[Resource]:
    resources = [Resource(type=ResourceType.CREW, name=f"Crew {i}", lat=lat, lng=lng)
                 for i, (lat, lng) in enumerate(CREWS, start=1)]
    resources += [Resource(type=ResourceType.SHELTER, name=name, lat=lat, lng=lng, capacity=beds)
                  for name, lat, lng, beds in SHELTERS]
    resources += [Resource(type=ResourceType.ADJUSTER, name=f"Adjuster {i}", lat=lat, lng=lng)
                  for i, (lat, lng) in enumerate(ADJUSTERS, start=1)]
    resources += [Resource(type=ResourceType.GENERATOR, name=f"Generator {i}", lat=lat, lng=lng)
                  for i, (lat, lng) in enumerate(GENERATORS, start=1)]
    return resources
