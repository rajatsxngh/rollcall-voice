"""Sanity report on the fake city: urgency mix, personas, and whether risk ranking beats list order.

    uv run python -m scripts.inspect_city              # data/runs/live.db
    uv run python -m scripts.inspect_city --db ../data/base.db
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from sqlmodel import select

from app.config import get_settings
from app.db import Database
from app.geo import WarningArea, find_affected
from app.models import CallPolicy, HiddenTruth, Household, Persona, Resource, Urgency
from app.risk import call_order, wave_for


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, help="database file (default: the live database)")
    args = parser.parse_args()
    settings = get_settings()
    db = Database(args.db or settings.live_db_path)
    with db.session() as session:
        households = session.exec(select(Household)).all()
        truths = {t.household_id: t for t in session.exec(select(HiddenTruth)).all()}
        resources = session.exec(select(Resource)).all()
    db.dispose()
    if not households:
        print("No households. Run: uv run python -m scripts.seed")
        return 1

    area = WarningArea.load(settings.data_dir / "warning_area.geojson")
    affected = find_affected(households, area)
    simulated = [h for h in households if h.id in truths]
    urgency_all = Counter(truths[h.id].true_urgency for h in simulated)
    urgency_in = Counter(truths[h.id].true_urgency for h in affected if h.id in truths)
    n, n_in = len(simulated), sum(urgency_in.values())

    print(f"households: {len(households):,} ({sum(h.live for h in households)} live demo); "
          f"inside the warning area: {len(affected):,}")
    print("true urgency, whole city:  " + "  ".join(f"{u}: {urgency_all[u]} ({pct(urgency_all[u], n)})" for u in Urgency))
    print("true urgency, inside area: " + "  ".join(f"{u}: {urgency_in[u]} ({pct(urgency_in[u], n_in)})" for u in Urgency))
    p12 = urgency_all[Urgency.P1] + urgency_all[Urgency.P2]
    print(f"P1+P2 overall: {pct(p12, n)} (target ~5%); P3: {pct(urgency_all[Urgency.P3], n)} (target ~20%)")

    personas = Counter(t.persona_style for t in truths.values())
    traps = sum(c for p, c in personas.items() if p not in (Persona.NORMAL, Persona.NEVER_ANSWERS))
    print(f"traps: {traps} ({', '.join(f'{p} {c}' for p, c in sorted(personas.items()) if p not in (Persona.NORMAL, Persona.NEVER_ANSWERS))})")
    print(f"never answer: {personas[Persona.NEVER_ANSWERS]}; answer on a later try: "
          f"{sum(1 for t in truths.values() if (t.answers_on_attempt or 0) > 1)}")
    print("resources: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r.type for r in resources).items())))

    waves = Counter(wave_for(h.risk_score) for h in affected)
    print(f"waves inside the area: 1 = {waves[1]}, 2 = {waves[2]}, 3 = {waves[3]}")

    urgent = {h.id for h in affected if h.id in truths and truths[h.id].true_urgency in (Urgency.P1, Urgency.P2)}
    print(f"\nShare of the {len(urgent)} urgent (P1+P2) homes reached after calling the first N homes:")
    print(f"  {'first N':>10}  {'risk-ranked':>12}  {'list order':>11}")
    orders = {p: [h.id for h in call_order(affected, p)] for p in CallPolicy}
    for share in (0.1, 0.2, 0.3, 0.5):
        k = round(len(affected) * share)
        found = {p: len(urgent & set(order[:k])) for p, order in orders.items()}
        print(f"  {k:>6} ({share:.0%})  {pct(found[CallPolicy.RISK_RANKED], len(urgent)):>12}"
              f"  {pct(found[CallPolicy.LIST_ORDER], len(urgent)):>11}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
