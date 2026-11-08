# RollCall dashboard (M4)

The screen judges watch: 2,000 households as dots colored by status, the warning area, the five counters and the agent feed, all drawn from the backend's event stream (contract: [docs/API.md](../docs/API.md)).

## How to run

Needs Node 20+ and the backend running (see the repo README).

```bash
cd dashboard
npm install
npm run dev            # http://127.0.0.1:5173
```

The backend URL comes from `VITE_API_BASE` (default `http://127.0.0.1:8100`). To change it, copy `.env.example` to `.env.local` and edit it, or set it for one run:

```bash
VITE_API_BASE=http://127.0.0.1:8010 npm run dev           # bash
$env:VITE_API_BASE="http://127.0.0.1:8010"; npm run dev   # PowerShell
```

Pick a speed (1×, 10×, 30×, 60×), click **Tornado warning**, and **Reset** to start over. The connection indicator sits at the top right; the light/dark toggle is next to it.

Other commands: `npm run build` (type-check and production build into `dist/`), `npm test` (counter, event-handling and results-formatting tests), `npm run preview`.

No backend? `http://127.0.0.1:5173/?fixture` fakes a city and a run in the browser, for UI work only. The header then says FIXTURE DATA.

**Results (M9 test mode):** the **Live / Results** switch in the top bar shows `GET /results`: RollCall vs list order (median, p90 and every-urgent-home minutes with the ratio), the curve of urgent homes with help over time, the trap case side by side, the trap report, keywords vs Nemotron, reply delay and the PRD targets, each with its source and the run's notes. Build the results with `cd backend; uv run python -m scripts.run_eval`. With the fixture: `?fixture` (simulated, with a trap case), `?fixture=scripted` (no traps), `?fixture=noresults` (404).

## How it works

- Loads `GET /meta` and `GET /state`, plus recent `GET /events` to rebuild the feed (it pages back to the run's alert so the alert time and plan survive a reload), then streams `WS /ws?since=<last_event_id>`. After a drop it reconnects with the last event id it saw. On `run.reset`, or if the backend's event log started over, it reloads `/state`.
- Events update plain objects in `src/store/runStore.ts`. The screen catches up in batches, at most every 80 ms. The map restyles only the dots that changed, on one shared canvas; calling homes pulse on a second canvas below it.
- Status colors, styles and help action types come from `/meta`. Counters follow the "Counters" rules in docs/API.md (`computeCounts`). In development the console warns if they ever differ from `/state.counts`.
- Times show in `display_tz` from `/meta` (Central).

## Layout and where later panels go

```
src/
  api/         contract types and the HTTP + WebSocket transport
  live/        the store instance and the sync loop (load, stream, reconnect, reload)
  store/       run state, event handling, counters (and their tests)
  map/         Leaflet map, canvas household layer, legend, dot popup
  panels/      sidebar panels: counters, agent feed
  components/  top bar (Live/Results switch, controls, connection), status dot
  results/     M9 results view, curve chart, formatting and chart math (and their tests)
  fixture/     fake backend behind ?fixture (fixtureResults.ts: a fake GET /results)
```

- **M6** live call transcript and P1 handoff card: new `<Panel>`s in the sidebar (`LiveView` in `App.tsx`), fed by `store.onEvent` (for example `call.transcript`).
- **M8** approvals queue: another sidebar panel. Household detail: clicking a dot already calls `store.select(id)`; render a panel from `snapshot.selectedId` in place of the popup in `map/MapView.tsx`.
- **M9** results / test mode: `results/ResultsView.tsx`, rendered in place of `LiveView` (the live sync keeps running behind it).

## Basemap

Stadia Maps "Alidade Smooth" (dark and light), which works from localhost without an API key. CARTO's free basemaps now answer "API key required". For other tiles, set `VITE_TILE_URL` and `VITE_TILE_ATTRIBUTION` (see `.env.example`, for example Esri World Dark Gray). Serving the dashboard from a real domain needs a Stadia API key or another provider.
