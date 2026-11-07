# RollCall API contract

The backend (`backend/`, FastAPI) and the dashboard (`dashboard/`) meet here. When a shape needs to change, change this file first, then both sides.

- Base URL: `http://127.0.0.1:8100`. CORS is open, so the dashboard's dev server can call it directly.
- Times are UTC ISO-8601 strings ending in `Z`. Show them in `display_tz` from `/meta` (Central).
- Statuses, colors, urgencies and event types come from `GET /meta`. Never hard-code them twice.

## Endpoints

| Method | Path | What it does | Built in |
|---|---|---|---|
| GET | `/health` | Liveness, last event id, which model providers are configured | M0 |
| GET | `/meta` | Shared vocabulary (below) | M0 |
| GET | `/state` | Snapshot of the whole run (below) | M2 |
| GET | `/events?since=&limit=&household_id=` | Event history after an id | M0 |
| WS | `/ws?since=<id>` | Replays events after `since`, then streams new ones | M0 |
| POST | `/alerts/demo?speed=<x>&lines=<n>&policy=<p>` | Starts the response on the saved warning area. `speed`: run-clock seconds per real second (default 10). `lines`: calls at once (default 100). `policy`: `risk_ranked` (default) or `list_order`. 409 while a run is active | M3 |
| POST | `/reset` | Stops the run, sets every household back to not called, clears needs, actions and calls, frees every resource, emits `run.reset` | M3 |
| POST | `/debug/emit` | Injects an event (scripts and development only) | M0 |
| POST | `/vapi/chat/completions`, `/vapi/webhook` | Phone platform (Vapi) | M1 |

The engine (M3) runs every timer on the run's clock, and **event times follow that clock**: at 60×, an hour of response takes a minute, and the feed's timestamps show response time. Until M6, calls go through a scripted channel that answers from the hidden truth with no AI.

## GET /meta

```json
{
  "display_tz": "America/Chicago",
  "statuses": [{"value": "not_called", "label": "Not called", "color": "#9ca3af", "style": "solid", "reached": false, "covered": false}],
  "urgencies": [{"value": "P1", "label": "Emergency", "status": "emergency", "color": "#ef4444"}],
  "event_types": [{"value": "household.status", "doc": "..."}],
  "resource_types": ["crew", "shelter", "adjuster", "generator"],
  "action_types": ["dispatch_crew", "..."],
  "help_action_types": ["deliver_generator", "dispatch_crew", "hotel_voucher", "in_person_check", "reserve_shelter_bed", "schedule_adjuster", "transfer_to_human"]
}
```

Status `style` values: `solid`, `pulse` (call in progress), `hollow` (no answer yet: outline only), `ring` (escalated to an in-person check: gray dot with a red ring).

## GET /state

```json
{
  "time": "2026-11-07T23:01:02.123Z",
  "run": {"active": true, "engine": "nemotron", "policy": "risk_ranked", "lines": 100, "speed": 60.0,
          "started_at": "2026-11-07T23:00:00.000Z", "finished_at": null, "clock": "2026-11-07T23:01:02.123Z"},
  "last_event_id": 1234,
  "warning_area": {"type": "Feature", "properties": {"title": "Tornado Warning", "issued_at": "2026-11-07T23:00:00.000Z", "event_id": 1201}, "geometry": {"type": "Polygon", "coordinates": [[[-96.8, 32.9], "..."]]}},
  "households": [
    {"id": 1, "name": "Maria Lopez", "address": "1412 Oak St, Richardson, TX", "lat": 32.95, "lng": -96.71,
     "status": "not_called", "risk_score": 62.0, "wave": null, "affected": true, "live": false}
  ],
  "resources": [{"id": 1, "type": "crew", "name": "Crew 1", "lat": 32.96, "lng": -96.73, "capacity": 1, "used": 0}],
  "helped_household_ids": [12, 87],
  "counts": {"households": 2000, "affected": 1500, "called": 0, "reached": 0, "urgent": 0, "help_sent": 0, "not_reached": 1500}
}
```

- `run` is `null` until a run starts; it stays (with `active: false`) after the first pass ends, until reset.
- `warning_area` is `null` until an alert has started the run. Before that, `affected` is `false` for every household.
- Phone numbers are never sent to the dashboard.
- Load `/state`, then connect to `/ws?since=<last_event_id>`. On a dropped connection, reconnect with the last event id you saw.

## Events

Every event has this envelope:

```json
{"id": 42, "time": "2026-11-07T23:01:02.123Z", "type": "household.status", "household_id": 17, "payload": {}}
```

**Feed rule:** any event whose payload has a `text` field is one line in the agent feed, in plain words, for example `"Sent Crew 3 to 1412 Oak St: oxygen machine without power."` Events without `text` (status changes, transcript lines) update the screen but are not feed lines.

Events the dashboard uses:

| Type | `household_id` | Payload |
|---|---|---|
| `alert.received` | null | `title`, `source`, `polygon` (GeoJSON Polygon geometry), `affected_ids` (list of household ids), `text` |
| `plan.created` | null | `summary`, `affected`, `waves` (e.g. `{"1": 312, "2": 540, "3": 648}`), `policy`, `lines`, `text`. With Nemotron, a first one without `text` starts the run and a second (`source: "nemotron"`) carries its one-line plan; use the latest |
| `call.started` | set | `call_id`, `channel`, `attempt`; phone calls add `status` (Vapi's first status) |
| `call.transcript` | set | `call_id`, `role` (`agent` or `resident`), `line` (the words; deliberately not `text`, so transcripts stay out of the feed) |
| `call.ended` | set | `call_id`, `outcome`, `summary`; phone calls add `ended_reason`, `duration_s` |
| `household.status` | set | `status`, `previous` |
| `need.recorded` | set | `category`, `details`, `urgency`, `call_id`, `need_id` (null if the household isn't in the database), `text` |
| `action.created` | set | `action_id`, `type`, `status`, `resource_id`, `reason`, `eta_minutes` (booked help), `text` (not for claims, follow-ups) |
| `action.updated` | set | `action_id`, `status` (e.g. `done` when a crew finishes) |
| `approval.requested` | set | `action_id`, `type`, `cost`, `reason`, `text` |
| `agent.note` | optional | `text`, optional `reason` (`replan`, `resources_busy`, `model_status`, `no_model`, `restart`), optional `change` (a re-plan), optional `tool` (the in-call tool that wrote it) |

For phone calls, `call_id` is the phone platform's call id (a string). Rows in the `calls` table for phone calls start in M6.
| `status.update` | null | `text`, `counts` |
| `run.reset` | null | `{}`: reload `/state` |

## Counters

Compute these the same way everywhere (the backend's `counts` follows the same rules):

- **affected**: households inside the current warning area.
- **called**: affected households whose status is not `not_called`.
- **reached**: households whose status has `reached: true` in `/meta`.
- **urgent**: status `urgent` or `emergency`.
- **help sent**: households with at least one action whose type is in `help_action_types`. Seed it from `helped_household_ids`, then add the household of every `action.created` event with a help type.
- **not reached yet**: affected households whose status is `not_called`, `calling` or `no_answer`.
