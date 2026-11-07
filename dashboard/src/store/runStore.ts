import type {
  Counts,
  Household,
  Meta,
  RunEvent,
  RunState,
  StatusMeta,
  StatusStyle,
  WarningArea,
} from '../api/types'

/**
 * The run as the dashboard sees it: the /state snapshot plus every event applied since.
 *
 * Events can arrive hundreds per second, so they mutate plain objects here and the screen
 * catches up in batches: React panels re-render at most every FLUSH_MS, and the map layer
 * gets the list of households whose status changed (see subscribeHouseholds).
 */

export type ConnState = 'connecting' | 'live' | 'reconnecting' | 'offline'

export interface FeedItem {
  id: number
  time: string
  type: string
  text: string
  householdId: number | null
  /** payload.type when present, e.g. the action type of action.created. */
  kind: string | null
  /** Map-style dot shown with the line: the urgency for needs, else the household's status. */
  dot: { color: string; style: StatusStyle } | null
}

export interface AlertInfo {
  title: string
  time: string | null
  source: string | null
}

export interface PlanInfo {
  summary: string
  waves: Record<string, number> | null
  affected: number | null
}

export interface UiSnapshot {
  ready: boolean
  conn: ConnState
  error: string | null
  meta: Meta | null
  counts: Counts
  /** Households per status: those in the warning area once there is one, else all. */
  statusCounts: Record<string, number>
  feed: FeedItem[]
  warningArea: WarningArea | null
  warningVersion: number
  alert: AlertInfo | null
  plan: PlanInfo | null
  runTime: string | null
  lastEventId: number
  selectedId: number | null
}

export interface HouseholdChange {
  /** Restyle everything (new snapshot, warning area changed, reset). */
  all: boolean
  ids: number[]
}

export const FEED_LIMIT = 300
const FLUSH_MS = 80

// Counter rules from docs/API.md ("Counters").
const NOT_REACHED_STATUSES = new Set(['not_called', 'calling', 'no_answer'])
const URGENT_STATUSES = new Set(['urgent', 'emergency'])

const UNKNOWN_STATUS: Omit<StatusMeta, 'value' | 'label'> = {
  color: '#9ca3af',
  style: 'solid',
  reached: false,
  covered: false,
}

export function emptyCounts(): Counts {
  return { households: 0, affected: 0, called: 0, reached: 0, urgent: 0, help_sent: 0, not_reached: 0 }
}

/** The five counters plus affected, computed exactly as docs/API.md describes. */
export function computeCounts(
  households: Iterable<Household>,
  statusByValue: Map<string, StatusMeta>,
  helped: Set<number>,
): Counts {
  const c = emptyCounts()
  for (const h of households) {
    c.households++
    if (h.affected) {
      c.affected++
      if (h.status !== 'not_called') c.called++
      if (NOT_REACHED_STATUSES.has(h.status)) c.not_reached++
    }
    if (statusByValue.get(h.status)?.reached) c.reached++
    if (URGENT_STATUSES.has(h.status)) c.urgent++
  }
  c.help_sent = helped.size
  return c
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function areaTitle(area: WarningArea | null): string {
  return str(area?.properties?.title) ?? 'Tornado Warning'
}

export class RunStore {
  meta: Meta | null = null
  statusByValue = new Map<string, StatusMeta>()
  helpTypes = new Set<string>()
  households = new Map<number, Household>()
  helped = new Set<number>()
  warningArea: WarningArea | null = null
  lastEventId = 0
  ready = false

  private warningVersion = 0
  private alert: AlertInfo | null = null
  private plan: PlanInfo | null = null
  private runTime: string | null = null
  private conn: ConnState = 'connecting'
  private error: string | null = null
  private selectedId: number | null = null

  private feed: FeedItem[] = []
  private pendingFeed: FeedItem[] = []
  private dirtyIds = new Set<number>()
  private dirtyAll = false
  private timer: ReturnType<typeof setTimeout> | null = null
  private lastFlush = 0

  private uiListeners = new Set<() => void>()
  private householdListeners = new Set<(change: HouseholdChange) => void>()
  private eventListeners = new Set<(event: RunEvent) => void>()
  private snapshot: UiSnapshot = this.buildSnapshot()

  // --- Subscriptions ------------------------------------------------------------------

  /** For useSyncExternalStore. */
  subscribe = (listener: () => void): (() => void) => {
    this.uiListeners.add(listener)
    return () => this.uiListeners.delete(listener)
  }

  getSnapshot = (): UiSnapshot => this.snapshot

  /** Batched household changes, for the map layer. */
  subscribeHouseholds(listener: (change: HouseholdChange) => void): () => void {
    this.householdListeners.add(listener)
    return () => this.householdListeners.delete(listener)
  }

  /** Every event as it is applied, unbatched. For later panels (live transcript, approvals). */
  onEvent(listener: (event: RunEvent) => void): () => void {
    this.eventListeners.add(listener)
    return () => this.eventListeners.delete(listener)
  }

  // --- Vocabulary ---------------------------------------------------------------------

  setMeta(meta: Meta): void {
    this.meta = meta
    this.statusByValue = new Map(meta.statuses.map((s) => [s.value, s]))
    this.helpTypes = new Set(meta.help_action_types)
    this.dirtyAll = true
    this.schedule()
  }

  statusOf(value: string): StatusMeta {
    return this.statusByValue.get(value) ?? { value, label: value.replace(/_/g, ' '), ...UNKNOWN_STATUS }
  }

  /** Red ring for escalated homes: the emergency color from /meta. */
  get ringColor(): string {
    return this.statusByValue.get('emergency')?.color ?? '#ef4444'
  }

  // --- Connection and selection -------------------------------------------------------

  setConn(conn: ConnState, error: string | null = null): void {
    if (this.conn === conn && this.error === error) return
    this.conn = conn
    this.error = error
    this.schedule(true)
  }

  select(id: number | null): void {
    if (this.selectedId === id) return
    this.selectedId = id
    this.schedule(true)
  }

  /** Clear the selection only if it is still `id` (a newer click may have replaced it). */
  deselect(id: number): void {
    if (this.selectedId === id) this.select(null)
  }

  // --- Loading and applying -----------------------------------------------------------

  /**
   * Replace everything with a /state snapshot. `history` is recent events up to the snapshot
   * (from GET /events): only their feed lines, alert and plan are used, since the snapshot
   * already includes their effects. Anything before the last run.reset is ignored.
   */
  loadSnapshot(state: RunState, history: RunEvent[]): void {
    this.households = new Map(state.households.map((h) => [h.id, { ...h }]))
    this.helped = new Set(state.helped_household_ids)
    this.warningArea = state.warning_area
    this.warningVersion++
    this.alert = state.warning_area ? { title: areaTitle(state.warning_area), time: null, source: null } : null
    this.plan = null
    this.feed = []
    this.pendingFeed = []

    let start = 0
    history.forEach((e, i) => {
      if (e.type === 'run.reset' && e.id <= state.last_event_id) start = i + 1
    })
    let newest: string | null = null
    for (const e of history.slice(start)) {
      if (e.id > state.last_event_id) break
      newest = e.time
      if (e.type === 'alert.received' && state.warning_area) this.alert = this.alertFrom(e)
      if (e.type === 'plan.created') this.plan = this.planFrom(e)
      const item = this.feedItem(e)
      if (item) this.pendingFeed.push(item)
    }

    if (this.selectedId != null && !this.households.has(this.selectedId)) this.selectedId = null
    this.lastEventId = state.last_event_id
    // The run clock is the time of the latest event (as it is live), not when the page loaded.
    this.runTime = newest ?? state.time ?? null
    this.ready = true
    this.dirtyAll = true
    this.schedule(true)

    if (import.meta.env.DEV) this.checkCounts(state)
  }

  /** Apply one live event. Returns false for an event already seen. */
  applyEvent(e: RunEvent): boolean {
    if (e.id <= this.lastEventId) return false
    this.lastEventId = e.id
    if (e.time) this.runTime = e.time
    const p = e.payload ?? {}

    switch (e.type) {
      case 'household.status': {
        const h = e.household_id != null ? this.households.get(e.household_id) : undefined
        if (h && typeof p.status === 'string' && h.status !== p.status) {
          h.status = p.status
          this.dirtyIds.add(h.id)
        }
        break
      }
      case 'alert.received': {
        if (p.polygon && typeof p.polygon === 'object') {
          this.warningArea = { type: 'Feature', properties: { title: str(p.title) }, geometry: p.polygon }
          this.warningVersion++
        }
        if (Array.isArray(p.affected_ids)) {
          const ids = new Set<number>(p.affected_ids)
          for (const h of this.households.values()) h.affected = ids.has(h.id)
        }
        this.alert = this.alertFrom(e)
        this.dirtyAll = true // dots outside the area dim
        break
      }
      case 'plan.created':
        this.plan = this.planFrom(e)
        break
      case 'action.created':
        if (e.household_id != null && this.helpTypes.has(p.type)) this.helped.add(e.household_id)
        break
      case 'run.reset':
        this.clearRun() // instant feedback; the connection then reloads /state
        break
    }

    const item = this.feedItem(e)
    if (item) this.pendingFeed.push(item)
    for (const listener of this.eventListeners) listener(e)
    this.schedule()
    return true
  }

  private clearRun(): void {
    for (const h of this.households.values()) {
      h.status = 'not_called'
      h.affected = false
      h.wave = null
    }
    this.helped.clear()
    this.warningArea = null
    this.warningVersion++
    this.alert = null
    this.plan = null
    this.feed = []
    this.pendingFeed = []
    this.dirtyAll = true
  }

  private alertFrom(e: RunEvent): AlertInfo {
    const p = e.payload ?? {}
    return { title: str(p.title) ?? areaTitle(this.warningArea), time: e.time, source: str(p.source) }
  }

  private planFrom(e: RunEvent): PlanInfo {
    const p = e.payload ?? {}
    return {
      summary: str(p.summary) ?? str(p.text) ?? '',
      waves: p.waves && typeof p.waves === 'object' ? (p.waves as Record<string, number>) : null,
      affected: typeof p.affected === 'number' ? p.affected : null,
    }
  }

  private feedItem(e: RunEvent): FeedItem | null {
    const text = str(e.payload?.text)
    if (!text) return null
    let dot: FeedItem['dot'] = null
    const urgency = e.type === 'need.recorded' ? this.meta?.urgencies.find((u) => u.value === e.payload.urgency) : undefined
    if (urgency) {
      dot = { color: urgency.color, style: 'solid' }
    } else if (e.household_id != null) {
      const h = this.households.get(e.household_id)
      if (h) {
        const s = this.statusOf(h.status)
        dot = { color: s.color, style: s.style }
      }
    }
    const kind = str(e.payload?.type)
    return { id: e.id, time: e.time, type: e.type, text, householdId: e.household_id, kind, dot }
  }

  // --- Batching -----------------------------------------------------------------------

  private schedule(immediate = false): void {
    if (this.timer != null) {
      if (!immediate) return
      clearTimeout(this.timer)
    }
    const wait = immediate ? 0 : Math.max(0, FLUSH_MS - (performance.now() - this.lastFlush))
    this.timer = setTimeout(this.flush, wait)
  }

  /** Push pending changes to listeners now. Normally runs on a timer; public for tests. */
  flush = (): void => {
    this.timer = null
    this.lastFlush = performance.now()

    if (this.pendingFeed.length) {
      const fresh = this.pendingFeed.reverse() // newest first
      this.pendingFeed = []
      this.feed =
        fresh.length >= FEED_LIMIT
          ? fresh.slice(0, FEED_LIMIT)
          : fresh.concat(this.feed.slice(0, FEED_LIMIT - fresh.length))
    }
    this.snapshot = this.buildSnapshot()
    for (const listener of this.uiListeners) listener()

    if (this.dirtyAll || this.dirtyIds.size) {
      const change: HouseholdChange = { all: this.dirtyAll, ids: [...this.dirtyIds] }
      this.dirtyAll = false
      this.dirtyIds.clear()
      for (const listener of this.householdListeners) listener(change)
    }
  }

  private buildSnapshot(): UiSnapshot {
    const statusCounts: Record<string, number> = {}
    const scoped = this.warningArea != null
    for (const h of this.households.values()) {
      if (scoped && !h.affected) continue
      statusCounts[h.status] = (statusCounts[h.status] ?? 0) + 1
    }
    return {
      ready: this.ready,
      conn: this.conn,
      error: this.error,
      meta: this.meta,
      counts: computeCounts(this.households.values(), this.statusByValue, this.helped),
      statusCounts,
      feed: this.feed,
      warningArea: this.warningArea,
      warningVersion: this.warningVersion,
      alert: this.alert,
      plan: this.plan,
      runTime: this.runTime,
      lastEventId: this.lastEventId,
      selectedId: this.selectedId,
    }
  }

  /** Development check: our counters should match the backend's for the same snapshot. */
  private checkCounts(state: RunState): void {
    if (!state.counts) return
    const mine = computeCounts(this.households.values(), this.statusByValue, this.helped)
    const diffs = (Object.keys(mine) as (keyof Counts)[]).filter((k) => mine[k] !== state.counts[k])
    if (diffs.length) console.warn('[rollcall] counters differ from /state counts', { mine, backend: state.counts })
  }
}
