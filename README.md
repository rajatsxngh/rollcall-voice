# RollCall Voice

RollCall Voice is an AI agent that phones every household in a disaster zone, works out who needs help most, and gets that help moving. It keeps calling back until every household is reached.

Built at HackUTD 2026 for the NVIDIA "Use Nemotron Beyond a Chatbot" and State Farm "The Worst Day, at Scale" tracks.

> **Status:** in progress. The build plan is in [MILESTONES.md](MILESTONES.md) and the product spec in [docs/PRD.md](docs/PRD.md). All household data is fake, and the large-scale test uses simulated residents.

## How to run

You need Python 3.11, [uv](https://docs.astral.sh/uv/), and (from M4) Node 20+.

```bash
cp .env.example .env    # then add your NVIDIA key (and an OpenRouter key for fallback)
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

The API runs on http://127.0.0.1:8000 (interactive docs at `/docs`).

Checks, run from `backend/`:

```bash
uv run pytest                                  # unit tests; no network or keys needed
uv run python -m scripts.smoke_llm             # Nemotron answers, with time to first token
uv run python -m scripts.smoke_llm --list-models
uv run python -m scripts.events_cli check      # event -> WebSocket -> history (backend running)
uv run python -m scripts.events_cli watch      # print live events
```

## How it fits together

- **Backend** (`backend/app`): FastAPI and SQLite. Every change is written to an event log and pushed over a WebSocket (`/ws`). The dashboard draws only from those events, so any run can be replayed.
- **Models** (`app/llm.py`): NVIDIA Nemotron through build.nvidia.com, with OpenRouter as the fallback. Nemotron 3 Super plans, a fast Nemotron model talks on the phone, and Nemotron Nano plays simulated residents in tests.
- **Shared vocabulary**: `GET /meta` lists household statuses with their map colors, urgency levels and event types.

## AI tools used

Per the MLH rules: this project is written with help from Claude Code (Anthropic) as an AI coding assistant. At runtime the agent uses NVIDIA Nemotron models.
