# Demo runbook

The demo has one moment: the judge's phone rings, they talk to the agent, and they watch their own home change on the map while help is sent. Everything here is about making that work on the first try.

## Before judging (about 20 minutes)

1. Phone hotspot on and the laptop connected (venue Wi-Fi drops).
2. Start everything, from the repo root, in three terminals:
   ```bash
   cd backend; uv run uvicorn app.main:app --port 8100
   ```
   ```bash
   cloudflared tunnel --url http://localhost:8100
   ```
   ```bash
   cd dashboard; npm run dev
   ```
3. If the tunnel URL changed, put it in `PUBLIC_BASE_URL` in `.env`, restart the backend, then run `uv run python -m scripts.vapi_setup`.
4. Set the demo phone (a teammate's verified phone that you hand to the judge):
   ```bash
   cd backend; uv run python -m scripts.demo_caller --name "<judge's first name>" --phone +1XXXXXXXXXX
   ```
5. Turn off debug endpoints: `DEBUG_ENDPOINTS=false` in `.env`, then restart the backend.
6. Preflight. Fix every FAIL:
   ```bash
   cd backend; uv run python -m scripts.preflight --call +1XXXXXXXXXX
   ```
7. In the dashboard: Reset, speed **30×** (the city resolves in about 2 minutes), and check the Results view loads.

## The 2 minutes (one person drives, one talks)

| Time | Show | Say |
|---|---|---|
| 0:00 | The map with 2,000 homes | "When a tornado hits Dallas, thousands of people are in its path. One person can check on maybe 30 an hour." |
| 0:15 | Click **Tornado warning** | "The agent finds everyone inside, ranks who's most at risk, and starts calling." |
| 0:30 | Dots change color, counters climb, the feed fills | "Nobody typed anything. It's running the response by itself." |
| 0:45 | The judge's phone rings | The judge talks to the agent; their dot turns orange; help appears in the feed; a text arrives. |
| 1:20 | **Results** view: the trap case | "Keyword logic marked her safe. Nemotron caught the oxygen machine." |
| 1:35 | **Results** view: the headline and the chart | "It found X of Y urgent cases in Z minutes. Calling in list order took W." (read the real numbers off the screen) |
| 1:50 | Back to Live: "not reached" at 0 | "Every home reached. No one left behind." |

Prompt the judge if they freeze: "Tell it your oxygen machine is beeping because the power's out."

## If something breaks

- **The call doesn't come:** keep going with the map and say "the call is ringing a verified test phone". Play the recorded video of a good call.
- **The models are slow or down:** the feed says decisions moved to the backup rules; the run keeps going. Say so; it's a feature.
- **No internet at all:** replay a recorded run (it needs no models or phones). After a good run during rehearsal, save it:
  ```bash
  curl -X POST "http://127.0.0.1:8100/recordings/save?name=good"
  ```
  Then on stage:
  ```bash
  curl -X POST "http://127.0.0.1:8100/recordings/good/play?speed=30"
  ```
  The map tiles need internet; with none, the dots still show on a blank background.
- **Anything else:** Reset and start the run again. It takes one click.

## Judge questions

- **Why AI and not a script?** Show the trap case: people describe emergencies in their own words. Keywords miss them; Nemotron catches them, and re-plans when things change.
- **Why Nemotron?** Open, fast and cheap enough for thousands of calls at once. A small model talks, a big model plans.
- **Is it safe?** It says it's automated, sends any danger to 911 and a human, never gives medical advice, and costly actions need approval. Real customers would opt in ahead of time.
- **What's real and what's simulated?** The call, the agent and the tools are real. Households, dispatch and claims are mocked, and the 2,000-household test is simulated.
- **Does it scale?** The limit is phone lines, not the agent. State the number of lines from the results.

## Rehearse 10 times

Tick one per clean run: ☐ ☐ ☐ ☐ ☐ ☐ ☐ ☐ ☐ ☐
