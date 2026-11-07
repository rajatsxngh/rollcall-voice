import { describe, expect, it } from 'vitest'
import type { Household, Meta, RunEvent, RunState } from '../api/types'
import { FEED_LIMIT, RunStore, computeCounts } from './runStore'

const GRAY = '#9ca3af'
const meta: Meta = {
  display_tz: 'America/Chicago',
  statuses: [
    { value: 'not_called', label: 'Not called', color: GRAY, style: 'solid', reached: false, covered: false },
    { value: 'calling', label: 'Calling', color: GRAY, style: 'pulse', reached: false, covered: false },
    { value: 'safe', label: 'Safe', color: '#22c55e', style: 'solid', reached: true, covered: true },
    { value: 'needs_help', label: 'Needs help', color: '#eab308', style: 'solid', reached: true, covered: true },
    { value: 'urgent', label: 'Urgent', color: '#f97316', style: 'solid', reached: true, covered: true },
    { value: 'emergency', label: 'Emergency', color: '#ef4444', style: 'solid', reached: true, covered: true },
    { value: 'no_answer', label: 'No answer', color: GRAY, style: 'hollow', reached: false, covered: false },
    { value: 'escalated', label: 'Escalated', color: GRAY, style: 'ring', reached: false, covered: true },
  ],
  urgencies: [{ value: 'P2', label: 'Urgent', status: 'urgent', color: '#f97316' }],
  event_types: [],
  resource_types: [],
  action_types: [],
  help_action_types: ['dispatch_crew', 'in_person_check', 'schedule_adjuster', 'transfer_to_human'],
}
const statusMap = new Map(meta.statuses.map((s) => [s.value, s]))
const polygon = { type: 'Polygon', coordinates: [[[-96.8, 32.9], [-96.6, 32.9], [-96.6, 33.0], [-96.8, 32.9]]] }

function hh(id: number, status: string, affected = true): Household {
  return { id, name: `H${id}`, address: `${id} Oak St`, lat: 32.95, lng: -96.71, status, risk_score: 50, wave: null, affected, live: false }
}

function snapshot(households: Household[], lastEventId = 10, extra: Partial<RunState> = {}): RunState {
  return {
    time: '2026-11-07T23:00:00.000Z',
    last_event_id: lastEventId,
    warning_area: null,
    households,
    resources: [],
    helped_household_ids: [],
    counts: computeCounts(households, statusMap, new Set(extra.helped_household_ids ?? [])),
    ...extra,
  }
}

let seq = 100
function ev(type: string, household_id: number | null, payload: Record<string, unknown> = {}, id = ++seq): RunEvent {
  return { id, time: '2026-11-07T23:01:00.000Z', type, household_id, payload }
}

function freshStore(households: Household[]): RunStore {
  const store = new RunStore()
  store.setMeta(meta)
  store.loadSnapshot(snapshot(households, 100), [])
  return store
}

describe('computeCounts (docs/API.md "Counters")', () => {
  it('counts affected, called, reached, urgent, help sent and not reached yet', () => {
    const homes = [
      hh(1, 'not_called'),
      hh(2, 'calling'),
      hh(3, 'safe'),
      hh(4, 'needs_help'),
      hh(5, 'urgent'),
      hh(6, 'emergency'),
      hh(7, 'no_answer'),
      hh(8, 'escalated'),
      hh(9, 'not_called', false),
    ]
    expect(computeCounts(homes, statusMap, new Set([5, 6, 8]))).toEqual({
      households: 9,
      affected: 8,
      called: 7, // affected and not "not_called"
      reached: 4, // safe, needs help, urgent, emergency
      urgent: 2,
      help_sent: 3,
      not_reached: 3, // not called, calling, no answer
    })
  })
})

describe('RunStore', () => {
  it('applies alert, status and help events, and builds the feed newest first', () => {
    seq = 100
    const store = freshStore([hh(1, 'not_called', false), hh(2, 'not_called', false)])
    store.applyEvent(ev('alert.received', null, { title: 'Tornado Warning', polygon, affected_ids: [1], text: 'Warning in.' }))
    store.applyEvent(ev('household.status', 1, { status: 'urgent', previous: 'not_called' }))
    const help = ev('action.created', 1, { type: 'dispatch_crew', text: 'Sent Crew 3 to 1 Oak St.' })
    expect(store.applyEvent(help)).toBe(true)
    expect(store.applyEvent(help)).toBe(false) // already seen
    store.applyEvent(ev('action.created', 2, { type: 'open_claim' })) // not a help type, no text
    store.applyEvent(ev('call.transcript', 1, { role: 'resident', line: 'Hello?' })) // never a feed line
    store.flush()

    const snap = store.getSnapshot()
    expect(snap.warningArea?.geometry).toEqual(polygon)
    expect(snap.counts).toMatchObject({ affected: 1, called: 1, reached: 1, urgent: 1, help_sent: 1, not_reached: 0 })
    expect(snap.feed.map((f) => f.text)).toEqual(['Sent Crew 3 to 1 Oak St.', 'Warning in.'])
    expect(snap.feed[0]).toMatchObject({ householdId: 1, kind: 'dispatch_crew', dot: { color: '#f97316', style: 'solid' } })
    expect(snap.lastEventId).toBe(seq)
  })

  it('ignores events at or below the snapshot id', () => {
    const store = freshStore([hh(1, 'not_called')])
    expect(store.applyEvent(ev('household.status', 1, { status: 'safe' }, 100))).toBe(false)
    expect(store.households.get(1)?.status).toBe('not_called')
  })

  it('run.reset clears the run at once', () => {
    seq = 100
    const store = freshStore([hh(1, 'not_called', false)])
    store.applyEvent(ev('alert.received', null, { polygon, affected_ids: [1], text: 'Warning in.' }))
    store.applyEvent(ev('household.status', 1, { status: 'safe' }))
    store.applyEvent(ev('run.reset', null, {}))
    store.flush()
    const snap = store.getSnapshot()
    expect(snap.warningArea).toBeNull()
    expect(snap.feed).toEqual([])
    expect(store.households.get(1)).toMatchObject({ status: 'not_called', affected: false })
  })

  it('rebuilds the feed from history after the last reset, capped and newest first', () => {
    const store = new RunStore()
    store.setMeta(meta)
    const history: RunEvent[] = [ev('agent.note', null, { text: 'old run' }, 1), ev('run.reset', null, {}, 2)]
    for (let id = 3; id < 3 + FEED_LIMIT + 20; id++) history.push(ev('agent.note', null, { text: `line ${id}` }, id))
    const last = history[history.length - 1].id
    store.loadSnapshot(snapshot([hh(1, 'safe')], last), history)
    store.flush()
    const feed = store.getSnapshot().feed
    expect(feed).toHaveLength(FEED_LIMIT)
    expect(feed[0].text).toBe(`line ${last}`)
    expect(feed.some((f) => f.text === 'old run')).toBe(false)
  })
})
