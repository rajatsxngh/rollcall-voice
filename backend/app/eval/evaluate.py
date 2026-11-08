"""Run the city twice (RollCall's risk-ranked order vs list order), score both against the hidden truth,
and write results/latest.json plus RESULTS.md.

Both runs replay the same calls and use the same triage (design decision 5), so only the calling
order differs. Calls come from the M7 simulator's store when it has them (Nemotron playing the
residents); otherwise they are scripted from the hidden truth, and the results say so plainly.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from app.city import copy_database
from app.clock import SimClock
from app.config import REPO_ROOT, Settings
from app.db import Database, iso_utc, utcnow
from app.engine.channels import CallResult
from app.engine.core import Engine
from app.engine.runs import city_seed, load_truths, make_brain
from app.eval.keywords import keyword_urgency
from app.eval.replay import ReplayChannel
from app.events import EventBus
from app.geo import WarningArea
from app.models import Call, CallPolicy, HiddenTruth, LLMUsage, NeedCategory, Persona, Status, Urgency

URGENT = (Urgency.P1, Urgency.P2)
FLAGGED = (Status.URGENT, Status.EMERGENCY)
RANK = {Urgency.P1: 1, Urgency.P2: 2, Urgency.P3: 3, Urgency.P4: 4}
RESULTS_DIR = REPO_ROOT / "results"


def load_simulated(settings: Settings, seed: int) -> tuple[dict[int, CallResult], dict[str, Any]]:
    """Stored simulated calls for this city, spec and models (empty if the simulator hasn't run)."""
    try:
        from app.sim.store import OutcomeStore, SimKey
    except ImportError:
        return {}, {}
    store = OutcomeStore.open(settings)
    try:
        key = SimKey.current(seed, settings)
        stored = store.load_all(key)
    finally:
        store.close()
    tokens = [s.total_tokens for s in stored.values() if s.model_calls]
    info = {"spec_hash": key.spec_hash, "voice_model": key.voice_model, "resident_model": key.resident_model,
            "tokens_per_household": round(statistics.mean(tokens)) if tokens else None}
    return {hid: s.result for hid, s in stored.items()}, info


async def _one_run(base: Path, out: Path, area: WarningArea, policy: CallPolicy, lines: int,
                   outcomes: dict[int, CallResult], llm: Any) -> dict[str, Any]:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{out}{suffix}").unlink(missing_ok=True)
    copy_database(base, out)
    db = Database(out, durable=False)
    try:
        clock = SimClock()
        bus = EventBus(db, now=clock.now)
        truths = load_truths(db)
        channel = ReplayChannel(clock, outcomes, truths, seed=city_seed(db))
        brain = make_brain(llm, db, bus)
        engine = Engine(db=db, bus=bus, clock=clock, area=area, channel=channel, triage=brain,
                        policy=policy, lines=lines)
        await engine.start()
        await clock.run()
        with db.session() as session:
            calls = session.exec(select(Call)).all()
        metrics = score(engine, truths, calls)
        metrics["_help_minutes"] = {hid: (t - engine.started_at).total_seconds() / 60 for hid, t in engine.help_at.items()}
        metrics["_status"] = {hid: h.status for hid, h in engine.households.items()}
        metrics["_triage"] = brain.source
        metrics["_replayed"], metrics["_scripted"] = len(channel.replayed), len(channel.scripted)
        return metrics
    finally:
        db.dispose()


def _pct(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)], 1)


def score(engine: Engine, truths: dict[int, HiddenTruth], calls: list[Call]) -> dict[str, Any]:
    homes = {hid: h for hid, h in engine.households.items() if hid in truths}  # live demo homes aren't scored
    true_urgent = {hid for hid in homes if truths[hid].true_urgency in URGENT}
    flagged = {hid for hid, h in homes.items() if h.status in FLAGGED}
    covered = {hid for hid, h in homes.items() if h.status not in (Status.NOT_CALLED, Status.CALLING, Status.NO_ANSWER)}
    waits = [(engine.help_at[hid] - engine.started_at).total_seconds() / 60 for hid in true_urgent if hid in engine.help_at]
    lengths = [(c.ended_at - c.started_at).total_seconds() / 60 for c in calls if c.ended_at and c.outcome and c.outcome.value == "completed"]
    end = engine.finished_at or engine.clock.now()
    return {
        "affected": len(homes), "calls": engine.calls_placed, "coverage_pct": _pct(len(covered), len(homes)),
        "true_urgent": len(true_urgent), "urgent_found": len(true_urgent & flagged),
        "urgent_found_pct": _pct(len(true_urgent & flagged), len(true_urgent)),
        "flagged_urgent": len(flagged), "false_alarms": len(flagged - true_urgent),
        "false_alarm_pct": _pct(len(flagged - true_urgent), len(flagged)),
        "true_urgent_helped": len(waits),
        "median_minutes_to_help": round(statistics.median(waits), 1) if waits else None,
        "p90_minutes_to_help": _percentile(waits, 0.9),
        "minutes_until_all_urgent_helped": round(max(waits), 1) if waits else None,
        "avg_call_minutes": round(statistics.mean(lengths), 2) if lengths else None,
        "minutes_total": round((end - engine.started_at).total_seconds() / 60, 1),
    }


def _curves(runs: dict[str, dict[str, Any]], truths: dict[int, HiddenTruth]) -> None:
    """Urgent homes with help on the way by each minute, for both runs on the same minutes."""
    urgent = {hid for hid, t in truths.items() if t.true_urgency in URGENT}
    last = max((m for r in runs.values() for hid, m in r["_help_minutes"].items() if hid in urgent), default=0)
    step = 1 if last <= 120 else 5
    minutes = list(range(0, int(math.ceil(last)) + step, step))
    for r in runs.values():
        times = sorted(m for hid, m in r["_help_minutes"].items() if hid in urgent)
        r["curve"] = [[m, sum(1 for t in times if t <= m)] for m in minutes]


def _trap_rule(persona: Persona, truth: HiddenTruth, status: Status, result: CallResult | None) -> tuple[bool, str]:
    if persona is Persona.DOWNPLAYS:
        return status in FLAGGED, "flagged P1 or P2 despite \"I'm fine\""
    if persona is Persona.CALL_DAUGHTER:
        saved = bool(result and any(n.category is NeedCategory.CONTACT for n in result.needs))
        return saved and (status is not Status.SAFE or truth.true_urgency is Urgency.P4), "saved the second contact, never guessed safe"
    if persona is Persona.HANGS_UP:
        return status is not Status.SAFE or truth.true_urgency is Urgency.P4, "followed up, not marked safe"
    rule = "finished or escalated, never guessed safe"
    if truth.true_urgency in URGENT:
        return status in FLAGGED or status is Status.ESCALATED, rule
    return status is not Status.SAFE or truth.true_urgency is Urgency.P4, rule


def _traps(truths: dict[int, HiddenTruth], outcomes: dict[int, CallResult], statuses: dict[int, Status],
           names: dict[int, str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows, case = [], None
    for hid, truth in sorted(truths.items()):
        if truth.persona_style in (Persona.NORMAL, Persona.NEVER_ANSWERS) or hid not in outcomes:
            continue
        result = outcomes[hid]
        status = statuses.get(hid, Status.NOT_CALLED)
        passed, rule = _trap_rule(truth.persona_style, truth, status, result)
        keyword = keyword_urgency(result.transcript)
        rows.append({"household_id": hid, "persona": truth.persona_style.value, "true_urgency": truth.true_urgency.value,
                     "found_urgency": result.urgency.value if result.urgency else None, "final_status": status.value,
                     "keyword_urgency": keyword.value, "passed": passed, "rule": rule, "summary": result.summary})
        if (case is None and truth.persona_style is Persona.DOWNPLAYS and passed and RANK[keyword] > 2):
            lines = [t["line"] for t in result.transcript if t.get("role") == "resident"]
            quote = next((ln for ln in lines if "fine" in ln.lower()), lines[0] if lines else "")
            case = {"household_id": hid, "name": names.get(hid, ""), "persona": "downplays", "quote": quote,
                    "keyword_said": f"{keyword.value} {'safe' if keyword is Urgency.P4 else ''}".strip(),
                    "nemotron_said": f"{result.urgency.value if result.urgency else '?'}: {result.summary}",
                    "transcript": result.transcript}
    return rows, case


def _triage_compare(truths: dict[int, HiddenTruth], outcomes: dict[int, CallResult],
                    statuses: dict[int, Status]) -> dict[str, Any] | None:
    judged = [hid for hid, r in outcomes.items() if hid in truths and r.reached and r.transcript]
    if not judged:
        return None
    urgent = {hid for hid in judged if truths[hid].true_urgency in URGENT}
    ours = {hid for hid in judged if statuses.get(hid) in FLAGGED}
    words = {hid for hid in judged if keyword_urgency(outcomes[hid].transcript) in URGENT}
    rate = lambda flagged: {"urgent_found_pct": _pct(len(urgent & flagged), len(urgent)),  # noqa: E731
                            "false_alarm_pct": _pct(len(flagged - urgent), len(flagged))}
    return {"households": len(judged), "nemotron": rate(ours), "keywords": rate(words)}


def _latency(settings: Settings) -> dict[str, Any] | None:
    if not settings.live_db_path.exists():
        return None
    db = Database(settings.live_db_path)
    try:
        with db.session() as session:
            ttfts = [u.ttft_ms for u in session.exec(select(LLMUsage).where(LLMUsage.job == "voice")).all() if u.ttft_ms]
    except Exception:
        return None
    finally:
        db.dispose()
    if not ttfts:
        return None
    return {"turns": len(ttfts), "p50_ms": round(statistics.median(ttfts)), "p90_ms": round(_percentile(ttfts, 0.9))}


async def evaluate(settings: Settings, *, lines: int = 100, llm: Any = None, base: Path | None = None,
                   use_simulated: bool = True) -> dict[str, Any]:
    from app.city import base_db_path

    base = base or base_db_path(settings)
    area = WarningArea.load(settings.data_dir / "warning_area.geojson")
    db = Database(base)
    truths, seed = load_truths(db), city_seed(db)
    with db.session() as session:
        from app.models import Household
        names = {h.id: h.name for h in session.exec(select(Household)).all()}
    db.dispose()
    outcomes, sim_info = load_simulated(settings, seed) if use_simulated else ({}, {})

    runs: dict[str, dict[str, Any]] = {}
    for policy in (CallPolicy.RISK_RANKED, CallPolicy.LIST_ORDER):
        runs[policy.value] = await _one_run(base, settings.runs_dir / f"eval_{policy.value}.db", area, policy, lines,
                                            outcomes, llm)
    _curves(runs, truths)
    ranked, listed = runs["risk_ranked"], runs["list_order"]
    statuses = ranked["_status"]
    traps, case = _traps(truths, outcomes, statuses, names)
    replayed, scripted = ranked["_replayed"], ranked["_scripted"]
    calls = "simulated" if replayed and not scripted else "mixed" if replayed else "scripted"

    def ratio(key: str) -> float | None:
        a, b = ranked.get(key), listed.get(key)
        return round(b / a, 1) if a and b else None

    results = {
        "generated_at": iso_utc(utcnow()),
        "source": {"calls": calls, "simulated_households": replayed, "scripted_households": scripted,
                   "triage": ranked["_triage"], "lines": lines, "seed": seed, **sim_info},
        "runs": {k: {m: v for m, v in r.items() if not m.startswith("_")} for k, r in runs.items()},
        "speedup": {"median": ratio("median_minutes_to_help"), "p90": ratio("p90_minutes_to_help"),
                    "all": ratio("minutes_until_all_urgent_helped")},
        "triage_compare": _triage_compare(truths, outcomes, statuses),
        "traps": traps, "trap_case": case, "latency": _latency(settings),
    }
    results["targets"] = _targets(results)
    results["notes"] = _notes(results)
    return results


def _targets(r: dict[str, Any]) -> list[dict[str, Any]]:
    ours = r["runs"]["risk_ranked"]
    out = [
        {"name": "Urgent households found (P1+P2)", "target": ">= 95%", "value": f"{ours['urgent_found_pct']}%",
         "met": (ours["urgent_found_pct"] or 0) >= 95},
        {"name": "Time until urgent homes have help", "target": "much faster than list order",
         "value": f"median {r['speedup']['median']}x faster" if r["speedup"]["median"] else "n/a",
         "met": (r["speedup"]["median"] or 0) > 1.5},
        {"name": "False alarms", "target": "<= 10%", "value": f"{ours['false_alarm_pct']}%",
         "met": (ours["false_alarm_pct"] if ours["false_alarm_pct"] is not None else 100) <= 10},
        {"name": "Households reached or escalated", "target": "100%", "value": f"{ours['coverage_pct']}%",
         "met": ours["coverage_pct"] == 100.0},
    ]
    if r["latency"]:
        out.append({"name": "Reply delay on real calls (p50)", "target": "under ~1.5 s",
                    "value": f"{r['latency']['p50_ms']} ms", "met": r["latency"]["p50_ms"] < 1500})
    return out


def _notes(r: dict[str, Any]) -> list[str]:
    s = r["source"]
    notes = [f"{s['lines']} phone lines; every run uses the same fake city (seed {s['seed']}) on a simulated clock."]
    if s["calls"] == "scripted":
        notes.append("Calls were scripted straight from each household's hidden truth (no simulated residents yet), "
                     "so urgent-found and false-alarm numbers here say nothing about the voice agent. "
                     "Only the calling-order comparison is meaningful.")
    else:
        notes.append(f"The 2,000-household test is simulated: Nemotron played {s['simulated_households']:,} residents"
                     + (f"; {s['scripted_households']:,} more were scripted from the hidden truth." if s["scripted_households"] else "."))
    notes.append("Both runs replay the same calls and use the same triage; only the calling order differs.")
    if r["triage_compare"]:
        notes.append("The keyword baseline is our own simple word-matching script, run on the same transcripts.")
    notes.append(f"Decisions: {s['triage']}.")
    return notes


def write_results(results: dict[str, Any], out_dir: Path = RESULTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    latest = out_dir / "latest.json"
    payload = json.dumps(results, indent=2, default=str)
    latest.write_text(payload, encoding="utf-8")
    (out_dir / f"results-{stamp}.json").write_text(payload, encoding="utf-8")
    md = REPO_ROOT / "RESULTS.md" if out_dir == RESULTS_DIR else out_dir / "RESULTS.md"  # tests stay out of the repo
    md.write_text(render_markdown(results), encoding="utf-8")
    return latest, md


def render_markdown(r: dict[str, Any]) -> str:
    a, b = r["runs"]["risk_ranked"], r["runs"]["list_order"]
    rows = [
        ("Homes in the warning area", "affected"), ("Calls placed", "calls"), ("Reached or escalated (%)", "coverage_pct"),
        ("Truly urgent homes (P1+P2)", "true_urgent"), ("Urgent found", "urgent_found"), ("Urgent found (%)", "urgent_found_pct"),
        ("False alarms (%)", "false_alarm_pct"), ("Median minutes until an urgent home had help", "median_minutes_to_help"),
        ("90th percentile minutes", "p90_minutes_to_help"), ("Minutes until every urgent home had help", "minutes_until_all_urgent_helped"),
        ("Average call length (min)", "avg_call_minutes"), ("Minutes until every home was accounted for", "minutes_total"),
    ]
    lines = [f"# RollCall results", "", f"Generated {r['generated_at']}. Calls: **{r['source']['calls']}**; "
             f"decisions: **{r['source']['triage']}**; {r['source']['lines']} phone lines.", "",
             "| | RollCall (risk-ranked) | List order |", "|---|---|---|"]
    lines += [f"| {label} | {a.get(k)} | {b.get(k)} |" for label, k in rows]
    sp = r["speedup"]
    lines += ["", f"Speedup (list order ÷ RollCall): median {sp['median']}×, 90th percentile {sp['p90']}×, every urgent home {sp['all']}×.", ""]
    lines += ["## Targets", "", "| Target | Goal | Result | Met |", "|---|---|---|---|"]
    lines += [f"| {t['name']} | {t['target']} | {t['value']} | {'yes' if t['met'] else 'no'} |" for t in r["targets"]]
    if r["triage_compare"]:
        tc = r["triage_compare"]
        lines += ["", "## Nemotron vs keywords (same transcripts)", "",
                  f"{tc['households']} answered calls. Nemotron: {tc['nemotron']['urgent_found_pct']}% urgent found, "
                  f"{tc['nemotron']['false_alarm_pct']}% false alarms. Keywords: {tc['keywords']['urgent_found_pct']}% found, "
                  f"{tc['keywords']['false_alarm_pct']}% false alarms."]
    if r["traps"]:
        by = Counter((t["persona"], t["passed"]) for t in r["traps"])
        personas = sorted({t["persona"] for t in r["traps"]})
        lines += ["", "## Trap set", "", "| Persona | Passed | Failed |", "|---|---|---|"]
        lines += [f"| {p} | {by[(p, True)]} | {by[(p, False)]} |" for p in personas]
    if r["latency"]:
        lines += ["", f"Reply delay on real calls: p50 {r['latency']['p50_ms']} ms, p90 {r['latency']['p90_ms']} ms "
                  f"over {r['latency']['turns']} turns."]
    lines += ["", "## Notes", ""] + [f"- {n}" for n in r["notes"]] + [""]
    return "\n".join(lines)
