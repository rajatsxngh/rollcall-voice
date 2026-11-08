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
uv run python -m scripts.seed           # build the fake city (2,000 households) into data/runs/live.db
uv run uvicorn app.main:app --port 8100 --reload
```

The API runs on http://127.0.0.1:8100 (interactive docs at `/docs`). Port 8100 rather than 8000, because 8000 is often taken by other local projects. `POST /alerts/demo` starts a run on the saved warning area and `POST /reset` clears it. The API contract is in [docs/API.md](docs/API.md).

Then the dashboard, in a second terminal:

```bash
cd dashboard
npm install
npm run dev                             # http://127.0.0.1:5173, talks to the backend on port 8100
```

Click **Tornado warning** to start a run. Until the phone (M6) and simulated residents (M7) are wired in, calls are scripted from the fake data, with no AI.

Checks, run from `backend/`:

```bash
uv run pytest                                  # unit tests; no network or keys needed
uv run python -m scripts.inspect_city          # urgency mix, traps, risk ranking vs list order
uv run python -m scripts.run_sim --compare     # whole city on a simulated clock, both call orders
uv run python -m scripts.run_eval              # M9 results vs list order -> results/latest.json, RESULTS.md
uv run python -m scripts.sim_calls --traps --dry-run   # M7 simulator (needs keys without --dry-run)
uv run python -m scripts.smoke_llm             # Nemotron answers, with time to first token
uv run python -m scripts.smoke_llm --list-models
uv run python -m scripts.events_cli check      # event -> WebSocket -> history (backend running)
uv run python -m scripts.events_cli watch      # print live events
```

### A real phone call (M1)

You need an NVIDIA key, a Vapi key, and a phone number imported into Vapi from Twilio, Vonage or Telnyx (free Vapi numbers can't place outbound calls, and a Twilio trial number can only call verified numbers). Then, from `backend/`:

```bash
uv run uvicorn app.main:app --port 8100
cloudflared tunnel --url http://localhost:8100     # put the https URL in PUBLIC_BASE_URL
uv run python -m scripts.vapi_setup                # creates the assistant; put VAPI_ASSISTANT_ID in .env
uv run python -m scripts.call_me +1XXXXXXXXXX "Maria"
```

Only call people who have agreed to it.

## How it fits together

- **Backend** (`backend/app`): FastAPI and SQLite. Every change is written to an event log and pushed over a WebSocket (`/ws`). The dashboard draws only from those events, so any run can be replayed.
- **Models** (`app/llm.py`): NVIDIA Nemotron through build.nvidia.com, with OpenRouter as the fallback. Nemotron 3 Super plans, a fast Nemotron model talks on the phone, and Nemotron Nano plays simulated residents in tests.
- **Shared vocabulary**: `GET /meta` lists household statuses with their map colors, urgency levels and event types.
- **Dashboard** (`dashboard/`): React, Vite, TypeScript and Leaflet. It loads `GET /state`, then streams `/ws`, and rebuilds itself from events after any reload or reconnect.

## AI tools used

Per the MLH rules: this project is written with help from Claude Code (Anthropic) as an AI coding assistant. At runtime the agent uses NVIDIA Nemotron models.
