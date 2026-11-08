import { ApiError, type StreamHandlers, type Transport } from '../api/transport'
import { fixtureResults } from './fixtureResults'
import type {
  ApprovalItem,
  AreaGeometry,
  FollowUp,
  Household,
  HouseholdAction,
  HouseholdCall,
  HouseholdDetail,
  HouseholdNeed,
  HouseholdProfile,
  Meta,
  RiskFactor,
  RunEvent,
  RunState,
  TranscriptLine,
  WarningArea,
} from '../api/types'
import { computeCounts } from '../store/runStore'

/*
 * Development stand-in for the backend, enabled with ?fixture in the URL. It mirrors the
 * /meta vocabulary and fakes a run (alert, plan, calls, needs, help) in the browser, so the UI
 * can be built and stress-tested without the API. The real dashboard never uses it.
 */

const GRAY = '#9ca3af'
const GREEN = '#22c55e'
const YELLOW = '#eab308'
const ORANGE = '#f97316'
const RED = '#ef4444'

const META: Meta = {
  display_tz: 'America/Chicago',
  statuses: [
    { value: 'not_called', label: 'Not called', color: GRAY, style: 'solid', reached: false, covered: false },
    { value: 'calling', label: 'Calling', color: GRAY, style: 'pulse', reached: false, covered: false },
    { value: 'safe', label: 'Safe', color: GREEN, style: 'solid', reached: true, covered: true },
    { value: 'needs_help', label: 'Needs help', color: YELLOW, style: 'solid', reached: true, covered: true },
    { value: 'urgent', label: 'Urgent', color: ORANGE, style: 'solid', reached: true, covered: true },
    { value: 'emergency', label: 'Emergency', color: RED, style: 'solid', reached: true, covered: true },
    { value: 'no_answer', label: 'No answer', color: GRAY, style: 'hollow', reached: false, covered: false },
    { value: 'escalated', label: 'Escalated', color: GRAY, style: 'ring', reached: false, covered: true },
  ],
  urgencies: [
    { value: 'P1', label: 'Emergency', status: 'emergency', color: RED },
    { value: 'P2', label: 'Urgent', status: 'urgent', color: ORANGE },
    { value: 'P3', label: 'Needs help', status: 'needs_help', color: YELLOW },
    { value: 'P4', label: 'Safe', status: 'safe', color: GREEN },
  ],
  event_types: [],
  resource_types: ['crew', 'shelter', 'adjuster', 'generator'],
  action_types: [],
  help_action_types: [
    'deliver_generator',
    'dispatch_crew',
    'hotel_voucher',
    'in_person_check',
    'reserve_shelter_bed',
    'schedule_adjuster',
    'transfer_to_human',
  ],
}

const POLYGON: AreaGeometry = {
  type: 'Polygon',
  coordinates: [
    [
      [-96.778, 32.932],
      [-96.705, 32.912],
      [-96.642, 32.94],
      [-96.648, 33.0],
      [-96.718, 33.016],
      [-96.772, 32.992],
      [-96.778, 32.932],
    ],
  ],
}

const FIRST = ['Maria', 'James', 'Linda', 'Robert', 'Patricia', 'Michael', 'Jennifer', 'David', 'Elizabeth', 'William', 'Barbara', 'Carlos', 'Priya', 'Ahmed', 'Grace', 'Luis', 'Mei', 'Thomas', 'Susan', 'Joseph']
const LAST = ['Lopez', 'Smith', 'Johnson', 'Williams', 'Brown', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez', 'Wilson', 'Anderson', 'Taylor', 'Lee', 'Tran', 'Patel', 'Kim', 'Nguyen', 'Walker', 'Hall']
const STREETS = ['Oak St', 'Belt Line Rd', 'Campbell Rd', 'Arapaho Rd', 'Spring Valley Rd', 'Custer Pkwy', 'Collins Blvd', 'Plano Rd', 'Jupiter Rd', 'Glenville Dr', 'Abrams Rd', 'Coit Rd', 'Renner Rd', 'Lookout Dr', 'Main St']

type Outcome = 'emergency' | 'urgent' | 'needs_help'
const NEEDS: Record<Outcome, { category: string; details: string }[]> = {
  emergency: [
    { category: 'medical', details: 'oxygen machine without power and short of breath' },
    { category: 'injury', details: 'cut by broken glass and bleeding' },
    { category: 'home_damage', details: 'smells gas after the roof came down' },
  ],
  urgent: [
    { category: 'power', details: 'oxygen concentrator on battery, about two hours left' },
    { category: 'home_damage', details: 'tree through the roof, two kids under five at home' },
    { category: 'evacuation', details: 'uses a wheelchair and the ramp is blocked' },
  ],
  needs_help: [
    { category: 'home_damage', details: 'windows blown out, needs a tarp and an adjuster' },
    { category: 'shelter', details: 'no power and nowhere warm to sleep tonight' },
    { category: 'supplies', details: 'out of medicine and has no car' },
  ],
}
const URGENCY: Record<Outcome, string> = { emergency: 'P1', urgent: 'P2', needs_help: 'P3' }

const LINES = 60
const TICK_MS = 100
const MIN = 60_000
const COST: Record<string, number> = { deliver_generator: 800, hotel_voucher: 180, dispatch_crew: 150, in_person_check: 150 }
const DEVICES = ['oxygen concentrator', 'CPAP machine', 'power wheelchair', 'home dialysis']
const MOBILITY = ['uses a wheelchair', 'uses a walker', 'bedbound']
const LANGS = ['en', 'en', 'en', 'en', 'en', 'es', 'es', 'vi', 'zh', 'ko']

/** The profile and risk factors behind a fixture household's score (own RNG, so the city layout is unchanged). */
function makeProfile(rand: () => number): { profile: HouseholdProfile; factors: RiskFactor[] } {
  const age = 18 + Math.floor(Math.pow(rand(), 0.8) * 80)
  const profile: HouseholdProfile = {
    age,
    lives_alone: rand() < (age > 70 ? 0.55 : 0.25),
    medical_device: rand() < (age > 70 ? 0.3 : 0.06) ? DEVICES[Math.floor(rand() * DEVICES.length)] : null,
    mobility: rand() < (age > 75 ? 0.35 : 0.05) ? MOBILITY[Math.floor(rand() * MOBILITY.length)] : null,
    has_car: rand() > (age > 80 ? 0.6 : 0.15),
    kids_under_5: age < 45 && rand() < 0.3 ? 1 + Math.floor(rand() * 2) : 0,
    language: LANGS[Math.floor(rand() * LANGS.length)],
  }
  const factors: RiskFactor[] = []
  if (profile.medical_device) factors.push({ key: 'powered_device', label: 'Medical device that needs power', points: 30 })
  if (age >= 75) factors.push({ key: 'age_75_plus', label: 'Age 75 or older', points: 20 })
  else if (age >= 65) factors.push({ key: 'age_65_plus', label: 'Age 65 or older', points: 12 })
  if (profile.lives_alone) factors.push({ key: 'lives_alone', label: 'Lives alone', points: 15 })
  if (profile.mobility) factors.push({ key: 'mobility', label: 'Limited mobility', points: 15 })
  if (!profile.has_car) factors.push({ key: 'no_car', label: 'No car to leave', points: 10 })
  if (profile.kids_under_5) factors.push({ key: 'kids_under_5', label: 'Children under 5', points: 8 })
  if (profile.language !== 'en') factors.push({ key: 'language', label: 'Prefers a language other than English', points: 5 })
  return { profile, factors }
}

function transcriptFor(name: string, outcome: Outcome | 'safe', details: string): TranscriptLine[] {
  const first = name.split(' ')[0]
  const lines: TranscriptLine[] = [
    { role: 'agent', line: `Hi, this is RollCall with the city of Richardson. Is this ${first}?` },
    { role: 'resident', line: 'Yes, this is me.' },
    { role: 'agent', line: 'There is a tornado warning for your area. Are you safe right now?' },
  ]
  if (outcome === 'safe') {
    lines.push({ role: 'resident', line: "We're fine, we're in the hallway." }, { role: 'agent', line: 'Good. Stay inside until the warning ends.' })
  } else {
    lines.push(
      { role: 'resident', line: `Not really. ${details[0].toUpperCase()}${details.slice(1)}.` },
      { role: 'agent', line: outcome === 'emergency' ? 'Please call 911 now. I am connecting you to a dispatcher.' : "I'm sending help. Stay where you are." },
    )
  }
  return lines
}

function mulberry32(seed: number): () => number {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function inside(lng: number, lat: number, ring: number[][]): boolean {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

const street = (h: Household) => h.address.split(',')[0]

export function fixtureTransport(): Transport {
  const rand = mulberry32(42)
  const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]
  const statusMap = new Map(META.statuses.map((s) => [s.value, s]))

  const clusters: [number, number][] = [
    [32.955, -96.735],
    [32.975, -96.69],
    [32.94, -96.67],
    [32.99, -96.73],
    [32.925, -96.72],
    [33.005, -96.675],
  ]
  const households: Household[] = []
  for (let id = 1; id <= 2000; id++) {
    const [clat, clng] = rand() < 0.8 ? pick(clusters) : [32.96, -96.71]
    const spread = rand() < 0.8 ? 0.02 : 0.06
    households.push({
      id,
      name: `${pick(FIRST)} ${pick(LAST)}`,
      address: `${100 + Math.floor(rand() * 9800)} ${pick(STREETS)}, Richardson, TX`,
      lat: clat + (rand() + rand() + rand() - 1.5) * spread,
      lng: clng + (rand() + rand() + rand() - 1.5) * spread * 1.2,
      status: 'not_called',
      risk_score: Math.round(Math.pow(rand(), 1.6) * 1000) / 10,
      wave: null,
      affected: false,
      live: id === 7,
    })
  }
  const byId = new Map(households.map((h) => [h.id, h]))

  // M8: the detail behind each household, the follow-up ladder and the approvals queue.
  const prand = mulberry32(7)
  const profiles = new Map<number, { profile: HouseholdProfile; factors: RiskFactor[] }>()
  const neverAnswers = new Set<number>()
  for (const h of households) {
    const p = makeProfile(prand)
    profiles.set(h.id, p)
    h.risk_score = Math.min(100, p.factors.reduce((n, f) => n + f.points, 0) + Math.round(prand() * 80) / 10)
    if (prand() < 0.025) neverAnswers.add(h.id)
  }
  const needsOf = new Map<number, HouseholdNeed[]>()
  const callsOf = new Map<number, HouseholdCall[]>()
  const actionsOf = new Map<number, HouseholdAction[]>()
  const followups = new Map<number, FollowUp>()
  const pending = new Map<number, ApprovalItem>()
  const actionHome = new Map<number, number>()
  let later: { at: number; h: Household }[] = []
  let needSeq = 0
  const push = <T,>(map: Map<number, T[]>, id: number, item: T) => {
    const list = map.get(id)
    if (list) list.push(item)
    else map.set(id, [item])
  }
  const iso = () => new Date(clock).toISOString()

  const subs = new Set<StreamHandlers>()
  const log: RunEvent[] = []
  let nextId = 1
  let clock = Date.now()
  let warning: WarningArea | null = null
  let helped = new Set<number>()
  let loop: ReturnType<typeof setInterval> | null = null
  let queue: Household[] = []
  let active: { id: number; h: Household; attempt: number; endAt: number }[] = []
  const attempts = new Map<number, number>()
  let callSeq = 0
  let actionSeq = 0
  let nextUpdate = 0

  const counts = () => computeCounts(households, statusMap, helped)

  function emit(type: string, household_id: number | null, payload: Record<string, unknown>): void {
    const event: RunEvent = { id: nextId++, time: new Date(clock).toISOString(), type, household_id, payload }
    log.push(event)
    for (const s of subs) queueMicrotask(() => s.onEvent(event))
  }

  function setStatus(h: Household, status: string): void {
    if (h.status === status) return
    const previous = h.status
    h.status = status
    emit('household.status', h.id, { status, previous })
  }

  function act(h: Household, type: string, text: string, reason: string, resource: string | null = null): number {
    helped.add(h.id)
    const id = ++actionSeq
    actionHome.set(id, h.id)
    push(actionsOf, h.id, {
      id,
      type,
      status: 'scheduled',
      cost: COST[type] ?? null,
      reason,
      needs_approval: false,
      resource,
      scheduled_for: new Date(clock + 20 * MIN).toISOString(),
      created_at: iso(),
    })
    emit('action.created', h.id, { action_id: id, type, status: 'scheduled', resource_id: null, reason, text })
    return id
  }

  /** A generator or hotel voucher: waits in the coordinator's queue. */
  function askApproval(h: Household, type: 'deliver_generator' | 'hotel_voucher', reason: string): void {
    const id = ++actionSeq
    const cost = COST[type]
    actionHome.set(id, h.id)
    push(actionsOf, h.id, { id, type, status: 'pending_approval', cost, reason, needs_approval: true, resource: null, scheduled_for: null, created_at: iso() })
    pending.set(id, {
      action_id: id,
      household_id: h.id,
      name: h.name,
      address: h.address,
      type,
      cost,
      reason,
      urgency_status: h.status,
      created_at: iso(),
    })
    const what = type === 'deliver_generator' ? 'a generator' : 'a hotel voucher'
    emit('approval.requested', h.id, { action_id: id, type, cost, reason, text: `Waiting for approval: ${what} for ${street(h)} ($${cost}), ${reason}.` })
  }

  function scheduleFollowUp(h: Household, step: number, channel: string, delayMs: number): void {
    const f = { step, channel, at: new Date(clock + delayMs).toISOString() }
    followups.set(h.id, f)
    emit('followup.scheduled', h.id, f)
    if (channel === 'call') later.push({ at: clock + delayMs, h })
  }

  function finish(call: { id: number; h: Household; attempt: number }): void {
    const { h } = call
    const record = callsOf.get(h.id)?.find((c) => c.id === call.id)
    if (record) record.ended_at = iso()
    followups.delete(h.id)
    if (neverAnswers.has(h.id) || rand() < 0.1) {
      if (record) Object.assign(record, { outcome: 'no_answer', summary: 'No answer.' })
      emit('call.ended', h.id, { call_id: call.id, outcome: 'no_answer', summary: 'No answer' })
      if (call.attempt === 1) {
        setStatus(h, 'no_answer')
        scheduleFollowUp(h, 1, 'call', 10 * MIN)
      } else if (call.attempt === 2) {
        setStatus(h, 'no_answer')
        emit('sms.sent', h.id, { body: 'RollCall: tornado warning. Reply 1 safe, 2 need help, 3 emergency.', step: 2 })
        scheduleFollowUp(h, 3, 'call', 20 * MIN)
      } else {
        setStatus(h, 'escalated')
        emit('followup.scheduled', h.id, { step: 4, channel: 'in_person', at: iso() })
        const crew = `Crew ${1 + Math.floor(rand() * 6)}`
        act(h, 'in_person_check', `No answer to two calls and a text at ${street(h)}: sending ${crew} for an in-person check.`, 'no answer after the follow-up ladder', crew)
      }
      return
    }
    const u = rand()
    const outcome: Outcome | 'safe' = u < 0.03 ? 'emergency' : u < 0.08 ? 'urgent' : u < 0.27 ? 'needs_help' : 'safe'
    const first = h.name.split(' ')[0]
    if (outcome === 'safe') {
      if (record) Object.assign(record, { outcome: 'completed', summary: `Spoke with ${first}: safe at home.`, transcript: transcriptFor(h.name, 'safe', '') })
      emit('call.ended', h.id, { call_id: call.id, outcome: 'completed', summary: '' })
      setStatus(h, 'safe')
      return
    }
    const need = pick(NEEDS[outcome])
    if (record) Object.assign(record, { outcome: 'completed', summary: `Spoke with ${first}: ${need.details}.`, transcript: transcriptFor(h.name, outcome, need.details) })
    emit('call.ended', h.id, { call_id: call.id, outcome: 'completed', summary: '' })
    push(needsOf, h.id, { id: ++needSeq, ...need, urgency: URGENCY[outcome], created_at: iso() })
    emit('need.recorded', h.id, {
      ...need,
      urgency: URGENCY[outcome],
      text: `${h.name} at ${street(h)}: ${need.details}.`,
    })
    setStatus(h, outcome)
    if (outcome === 'emergency') {
      act(h, 'transfer_to_human', `Told ${h.name} to call 911 and handed the call to a dispatcher.`, need.details)
    } else if (outcome === 'urgent') {
      const crew = `Crew ${1 + Math.floor(rand() * 6)}`
      act(h, 'dispatch_crew', `Sent ${crew} to ${street(h)}: ${need.details}.`, need.details, crew)
      if (need.category === 'power' && rand() < 0.4) askApproval(h, 'deliver_generator', 'oxygen concentrator needs power')
    } else if (need.category === 'shelter' && rand() < 0.35) {
      askApproval(h, 'hotel_voucher', 'no power and nowhere warm to sleep tonight')
    } else if (rand() < 0.6) {
      act(h, 'schedule_adjuster', `Booked an adjuster for ${street(h)} tomorrow morning.`, need.details, 'Adjuster 2')
    } else {
      act(h, 'reserve_shelter_bed', `Reserved a shelter bed at Richardson High for ${h.name}.`, need.details, 'Richardson High shelter')
    }
  }

  function step(simMs: number): void {
    clock += simMs
    active = active.filter((call) => {
      if (call.endAt > clock) return true
      finish(call)
      return false
    })
    if (later.length) {
      const due = later.filter((x) => x.at <= clock)
      if (due.length) {
        later = later.filter((x) => x.at > clock)
        queue.unshift(...due.map((x) => x.h))
      }
    }
    while (active.length < LINES && queue.length) {
      const h = queue.shift()!
      const attempt = (attempts.get(h.id) ?? 0) + 1
      attempts.set(h.id, attempt)
      const call = { id: ++callSeq, h, attempt, endAt: clock + (40 + rand() * 140) * 1000 }
      active.push(call)
      push(callsOf, h.id, { id: call.id, attempt, channel: 'scripted', outcome: null, started_at: iso(), ended_at: null, summary: null, transcript: [] })
      emit('call.started', h.id, { call_id: call.id, channel: 'scripted', attempt })
      setStatus(h, 'calling')
    }
    if (clock >= nextUpdate) {
      nextUpdate += 10 * 60_000
      const c = counts()
      emit('status.update', null, {
        counts: c,
        text: `Update: ${c.reached} of ${c.affected} homes reached, ${c.urgent} urgent, help sent to ${c.help_sent}. ${c.not_reached} not reached yet.`,
      })
    }
    if (!queue.length && !active.length && !later.length && loop) {
      clearInterval(loop)
      loop = null
      emit('agent.note', null, { text: `Every home in the warning area has been called. ${counts().not_reached} still need a follow-up.` })
    }
  }

  async function startDemo(speed: number): Promise<unknown> {
    if (warning) throw new Error('A warning is already running. Reset first.')
    clock = Date.now()
    warning = { type: 'Feature', properties: { title: 'Tornado Warning' }, geometry: POLYGON }
    const ring = (POLYGON.coordinates as number[][][])[0]
    const affected = households.filter((h) => inside(h.lng, h.lat, ring))
    affected.forEach((h) => (h.affected = true))
    emit('alert.received', null, {
      title: 'Tornado Warning',
      source: 'fixture',
      polygon: POLYGON,
      affected_ids: affected.map((h) => h.id),
      text: `Tornado warning for Richardson and Garland: ${affected.length} homes are inside the warning area.`,
    })
    queue = [...affected].sort((a, b) => b.risk_score - a.risk_score)
    const w1 = Math.round(queue.length * 0.2)
    const w2 = Math.round(queue.length * 0.35)
    const w3 = queue.length - w1 - w2
    queue.forEach((h, i) => (h.wave = i < w1 ? 1 : i < w1 + w2 ? 2 : 3))
    emit('plan.created', null, {
      summary: `${w1} high-risk homes first, in 3 waves`,
      affected: queue.length,
      waves: { 1: w1, 2: w2, 3: w3 },
      text: `Plan: call the ${w1} highest-risk homes first (older, alone, on powered medical devices), then ${w2}, then ${w3}.`,
    })
    nextUpdate = clock + 10 * 60_000
    loop = setInterval(() => step(TICK_MS * speed), TICK_MS)
    return { ok: true }
  }

  async function reset(): Promise<unknown> {
    if (loop) clearInterval(loop)
    loop = null
    queue = []
    active = []
    attempts.clear()
    later = []
    needsOf.clear()
    callsOf.clear()
    actionsOf.clear()
    followups.clear()
    pending.clear()
    actionHome.clear()
    helped = new Set()
    warning = null
    households.forEach((h) => {
      h.status = 'not_called'
      h.affected = false
      h.wave = null
    })
    clock = Date.now()
    emit('run.reset', null, {})
    return { ok: true }
  }

  async function decideApproval(actionId: number, approve: boolean): Promise<ApprovalItem> {
    const item = pending.get(actionId)
    if (!item) {
      if (actionHome.has(actionId)) throw new ApiError(409, 'already decided')
      throw new ApiError(404, 'no such action')
    }
    pending.delete(actionId)
    const h = byId.get(item.household_id)!
    const action = actionsOf.get(h.id)?.find((a) => a.id === actionId)
    const what = item.type === 'deliver_generator' ? 'generator' : 'hotel voucher'
    if (action) action.status = approve ? 'scheduled' : 'rejected'
    emit('approval.decided', h.id, {
      action_id: actionId,
      type: item.type,
      approved: approve,
      by: 'coordinator',
      text: `${approve ? 'Approved' : 'Rejected'} the ${what} for ${street(h)}.`,
    })
    if (approve) {
      if (action) {
        action.resource = item.type === 'deliver_generator' ? 'Generator 1' : 'Hyatt Regency Richardson'
        action.scheduled_for = new Date(clock + 45 * MIN).toISOString()
      }
      helped.add(h.id)
      emit('action.updated', h.id, { action_id: actionId, status: 'scheduled' })
    } else {
      act(h, 'reserve_shelter_bed', `Instead of a ${what}, reserved a shelter bed at Richardson High for ${h.name}.`, `${what} rejected by the coordinator`, 'Richardson High shelter')
    }
    return { ...item }
  }

  function getHousehold(id: number): HouseholdDetail {
    const h = byId.get(id)
    if (!h) throw new ApiError(404, 'no such household')
    const p = profiles.get(id)!
    return {
      ...h,
      risk_factors: p.factors,
      profile: p.profile,
      needs: [...(needsOf.get(id) ?? [])],
      calls: (callsOf.get(id) ?? []).map((c) => ({ ...c, transcript: [...(c.transcript ?? [])] })),
      actions: (actionsOf.get(id) ?? []).map((a) => ({ ...a })),
      next_followup: followups.get(id) ?? null,
    }
  }

  const lifeSafety = (a: ApprovalItem) => Number(a.urgency_status === 'emergency' || a.urgency_status === 'urgent')

  return {
    label: 'local fixture',
    getApprovals: async () => ({
      approvals: [...pending.values()].sort((a, b) => lifeSafety(b) - lifeSafety(a) || a.action_id - b.action_id),
    }),
    decideApproval,
    getHousehold: async (id) => getHousehold(id),
    getResults: async () => {
      const results = fixtureResults(new URLSearchParams(window.location.search).get('fixture'))
      if (!results) throw new ApiError(404, 'GET /results failed (404): no results yet')
      return results
    },
    getMeta: async () => META,
    getState: async (): Promise<RunState> => ({
      time: new Date(clock).toISOString(),
      last_event_id: nextId - 1,
      warning_area: warning,
      households: households.map((h) => ({ ...h })),
      resources: [],
      helped_household_ids: [...helped].filter((id) => byId.has(id)),
      counts: counts(),
    }),
    getEvents: async (since, limit) => ({ events: log.filter((e) => e.id > since).slice(0, limit), last_id: nextId - 1 }),
    getHealth: async () => ({ status: 'ok', last_event_id: nextId - 1 }),
    startDemo,
    reset,
    openStream(since, handlers) {
      const timer = setTimeout(() => {
        handlers.onOpen()
        for (const e of log) if (e.id > since) handlers.onEvent(e)
        subs.add(handlers)
      }, 50)
      return {
        close() {
          clearTimeout(timer)
          subs.delete(handlers)
        },
      }
    },
  }
}
