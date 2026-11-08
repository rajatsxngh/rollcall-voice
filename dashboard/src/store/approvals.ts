import type { ApprovalItem, RunEvent } from '../api/types'

/**
 * The coordinator's approvals queue (docs/API.md "GET /approvals"): loaded from the API, then
 * kept in step with events. approval.requested adds, approval.decided removes, run.reset
 * clears and reloads. Events that arrive while a load is in flight are re-applied on top of
 * its result, so a slow GET never brings back an item that was already decided.
 */

export const LIFE_SAFETY = new Set(['emergency', 'urgent'])

export const isLifeSafety = (item: Pick<ApprovalItem, 'urgency_status'>): boolean =>
  item.urgency_status != null && LIFE_SAFETY.has(item.urgency_status)

/** Life-safety first, then oldest first (the API's order). */
export function sortApprovals(items: ApprovalItem[]): ApprovalItem[] {
  return [...items].sort((a, b) => {
    const ls = Number(isLifeSafety(b)) - Number(isLifeSafety(a))
    if (ls) return ls
    return a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.action_id - b.action_id
  })
}

export interface ApprovalsSnapshot {
  items: ApprovalItem[]
  loaded: boolean
  /** Set when the last load failed (e.g. 404 from a backend without M8). */
  error: string | null
}

/** What approval.requested doesn't carry: filled from the run store's household. */
export type HouseholdLookup = (id: number) => { name: string; address: string; status: string } | undefined

export class ApprovalsStore {
  private items = new Map<number, ApprovalItem>()
  private loaded = false
  private error: string | null = null
  private generation = 0
  /** Events seen while a load is in flight, re-applied to its result. */
  private inFlight: RunEvent[] | null = null
  private listeners = new Set<() => void>()
  private snapshot: ApprovalsSnapshot = { items: [], loaded: false, error: null }

  constructor(
    private readonly loader: () => Promise<ApprovalItem[]>,
    private readonly lookup: HouseholdLookup = () => undefined,
  ) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): ApprovalsSnapshot => this.snapshot

  /** GET /approvals and replace the list. Resolves once applied (or dropped as stale). */
  async load(): Promise<void> {
    const gen = ++this.generation
    this.inFlight = []
    try {
      const list = await this.loader()
      if (gen !== this.generation) return
      const pending = this.inFlight ?? []
      this.inFlight = null
      this.items = new Map(list.map((a) => [a.action_id, a]))
      for (const e of pending) this.applyOne(e)
      this.loaded = true
      this.error = null
    } catch (err) {
      if (gen !== this.generation) return
      this.inFlight = null
      this.error = err instanceof Error ? err.message : String(err)
    }
    this.emit()
  }

  /** Feed every live event through here (RunStore.onEvent). */
  applyEvent(e: RunEvent): void {
    if (e.type === 'run.reset') {
      this.items.clear()
      this.emit()
      void this.load()
      return
    }
    if (e.type !== 'approval.requested' && e.type !== 'approval.decided') return
    this.inFlight?.push(e)
    if (this.applyOne(e)) this.emit()
  }

  /** Drop one item locally (after our own approve / reject succeeded). */
  remove(actionId: number): void {
    if (this.items.delete(actionId)) this.emit()
  }

  private applyOne(e: RunEvent): boolean {
    const p = e.payload ?? {}
    const actionId = typeof p.action_id === 'number' ? p.action_id : null
    if (actionId == null) return false
    if (e.type === 'approval.decided') return this.items.delete(actionId)
    if (this.items.has(actionId)) return false
    const h = e.household_id != null ? this.lookup(e.household_id) : undefined
    this.items.set(actionId, {
      action_id: actionId,
      household_id: e.household_id ?? 0,
      name: typeof p.name === 'string' ? p.name : (h?.name ?? ''),
      address: typeof p.address === 'string' ? p.address : (h?.address ?? ''),
      type: typeof p.type === 'string' ? p.type : 'action',
      cost: typeof p.cost === 'number' ? p.cost : null,
      reason: typeof p.reason === 'string' ? p.reason : '',
      urgency_status: typeof p.urgency_status === 'string' ? p.urgency_status : (h?.status ?? null),
      created_at: e.time,
    })
    return true
  }

  private emit(): void {
    this.snapshot = { items: sortApprovals([...this.items.values()]), loaded: this.loaded, error: this.error }
    for (const l of this.listeners) l()
  }
}
