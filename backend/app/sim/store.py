"""Stored simulated calls, so the long run survives crashes and M9 can replay them through the
engine.

One row per household per (city seed, voice spec hash, voice model, resident model). Changing the
voice prompt or tools changes voice_spec.SPEC_HASH, so old rows stop counting as stored and those
households run again; rows for the old spec stay in the file for comparison.

    store = OutcomeStore.open(settings)
    key = SimKey.current(seed, settings)
    store.save(key, run)
    store.load(key, household_id)      # -> StoredOutcome | None
    store.load_all(key)                # every stored household for this seed, spec and models
    store.count(key)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.agent import voice_spec
from app.config import Settings
from app.engine.channels import CallResult, NeedReport
from app.models import CallOutcome, Channel, NeedCategory, Urgency
from app.sim.conversation import SimRun


def store_path(settings: Settings) -> Path:
    return settings.data_dir / "cache" / "sim_outcomes.db"


def configured_providers(settings: Settings) -> list[str]:
    """Providers with a key, in LLMClient's fallback order."""
    return [p for p, ok in (("nvidia", bool(settings.nvidia_keys)),
                            ("openrouter", bool(settings.openrouter_api_key))) if ok]


def model_ids(settings: Settings, providers: list[str] | None = None) -> tuple[str, str]:
    """(voice model, resident model) on the first configured provider (nvidia unless only
    OpenRouter has a key). Replies served by the fallback provider are stored under the same key;
    each row also records the models that actually answered."""
    provider = (providers or ["nvidia"])[0]
    return settings.model_for(provider, "voice"), settings.model_for(provider, "resident")


@dataclass(frozen=True)
class SimKey:
    seed: int
    spec_hash: str
    voice_model: str
    resident_model: str

    @classmethod
    def current(cls, seed: int, settings: Settings, providers: list[str] | None = None) -> SimKey:
        voice, resident = model_ids(settings, configured_providers(settings) if providers is None else providers)
        return cls(seed, voice_spec.SPEC_HASH, voice, resident)


@dataclass
class StoredOutcome:
    result: CallResult
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    cached_calls: int = 0
    elapsed_s: float = 0.0
    ended_by: str = ""
    models: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# --- CallResult <-> JSON -------------------------------------------------------------------


def result_to_json(result: CallResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False)


def result_from_json(raw: str | dict[str, Any]) -> CallResult:
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    data["channel"] = Channel(data["channel"])
    data["outcome"] = CallOutcome(data["outcome"])
    data["urgency"] = Urgency(data["urgency"]) if data.get("urgency") else None
    data["needs"] = [
        NeedReport(NeedCategory(n["category"]), n["details"], Urgency(n["urgency"]))
        for n in data.get("needs") or []
    ]
    return CallResult(**data)


# --- store -----------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_outcomes (
    household_id INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    voice_model TEXT NOT NULL,
    resident_model TEXT NOT NULL,
    outcome TEXT NOT NULL,
    urgency TEXT,
    result TEXT NOT NULL,
    tool_calls TEXT NOT NULL DEFAULT '[]',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    model_calls INTEGER NOT NULL DEFAULT 0,
    cached_calls INTEGER NOT NULL DEFAULT 0,
    elapsed_s REAL NOT NULL DEFAULT 0,
    ended_by TEXT NOT NULL DEFAULT '',
    models TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (household_id, seed, spec_hash, voice_model, resident_model)
)
"""
_COLUMNS = ("result, tool_calls, prompt_tokens, completion_tokens, model_calls, cached_calls,"
            " elapsed_s, ended_by, models, created_at")
_WHERE = "seed = ? AND spec_hash = ? AND voice_model = ? AND resident_model = ?"


class OutcomeStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    @classmethod
    def open(cls, settings: Settings) -> OutcomeStore:
        return cls(store_path(settings))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, key: SimKey, run: SimRun | StoredOutcome) -> None:
        r = run.result
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sim_outcomes (household_id, seed, spec_hash, voice_model,"
                " resident_model, outcome, urgency, result, tool_calls, prompt_tokens,"
                " completion_tokens, model_calls, cached_calls, elapsed_s, ended_by, models)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.household_id, key.seed, key.spec_hash, key.voice_model, key.resident_model,
                    r.outcome.value, r.urgency.value if r.urgency else None, result_to_json(r),
                    json.dumps(run.tool_calls, ensure_ascii=False), run.prompt_tokens,
                    run.completion_tokens, run.model_calls, run.cached_calls, run.elapsed_s,
                    run.ended_by, json.dumps(run.models),
                ),
            )
            self._conn.commit()

    def load(self, key: SimKey, household_id: int) -> StoredOutcome | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM sim_outcomes WHERE household_id = ? AND {_WHERE}",
                (household_id, *_params(key)),
            ).fetchone()
        return _row(row) if row else None

    def load_all(self, key: SimKey) -> dict[int, StoredOutcome]:
        """Every stored household for this seed, spec and models, by household id."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT household_id, {_COLUMNS} FROM sim_outcomes WHERE {_WHERE}"
                " ORDER BY household_id",
                _params(key),
            ).fetchall()
        return {row[0]: _row(row[1:]) for row in rows}

    def stored_ids(self, key: SimKey) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT household_id FROM sim_outcomes WHERE {_WHERE}", _params(key)
            ).fetchall()
        return {r[0] for r in rows}

    def count(self, key: SimKey) -> int:
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM sim_outcomes WHERE {_WHERE}", _params(key)
            ).fetchone()[0]

    def latest(self, household_id: int, seed: int | None = None) -> tuple[SimKey, StoredOutcome] | None:
        """The newest stored call for a household under any spec or models (for `show`)."""
        sql = (f"SELECT seed, spec_hash, voice_model, resident_model, {_COLUMNS} FROM sim_outcomes"
               " WHERE household_id = ?")
        params: list[Any] = [household_id]
        if seed is not None:
            sql += " AND seed = ?"
            params.append(seed)
        with self._lock:
            row = self._conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", params).fetchone()
        return (SimKey(*row[:4]), _row(row[4:])) if row else None


def _params(key: SimKey) -> tuple[Any, ...]:
    return key.seed, key.spec_hash, key.voice_model, key.resident_model


def _row(row: tuple[Any, ...]) -> StoredOutcome:
    (result, tool_calls, prompt, completion, calls, cached, elapsed, ended_by, models, created) = row
    return StoredOutcome(
        result=result_from_json(result),
        tool_calls=json.loads(tool_calls or "[]"),
        prompt_tokens=prompt,
        completion_tokens=completion,
        model_calls=calls,
        cached_calls=cached,
        elapsed_s=elapsed,
        ended_by=ended_by,
        models=json.loads(models or "{}"),
        created_at=created,
    )
