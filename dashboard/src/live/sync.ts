import type { StreamHandle, Transport } from '../api/transport'
import type { RunEvent } from '../api/types'
import type { RunStore } from '../store/runStore'

/** How many recent events to read per page on load to rebuild the agent feed (API max 5000). */
const HISTORY_WINDOW = 4000
/** Extra pages to look back for the current run's alert. */
const MAX_HISTORY_PAGES = 6
const MAX_RETRY_MS = 5000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const describe = (err: unknown) => (err instanceof Error ? err.message : String(err))

/**
 * Keeps the store in step with the backend, per docs/API.md:
 * GET /meta and /state, then /ws?since=<last_event_id>. On a drop it reconnects with the last
 * event id it saw; on run.reset (or if the backend's event log restarted) it reloads /state.
 */
export class LiveSync {
  private stream: StreamHandle | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private attempts = 0
  private running = false
  /** Bumped on stop and on every resync, so stale async work and sockets are ignored. */
  private generation = 0

  constructor(
    private readonly store: RunStore,
    private readonly transport: Transport,
  ) {}

  start(): void {
    if (this.running) return
    this.running = true
    void this.resync()
  }

  stop(): void {
    this.running = false
    this.generation++
    this.clearRetry()
    this.stream?.close()
    this.stream = null
  }

  /** Load the snapshot (and recent feed lines), then stream from its last event id. */
  async resync(minEventId = 0): Promise<void> {
    const gen = ++this.generation
    this.clearRetry()
    this.stream?.close()
    this.stream = null
    this.store.setConn(this.store.ready ? 'reconnecting' : 'connecting')
    try {
      if (!this.store.meta) this.store.setMeta(await this.transport.getMeta())
      let state = await this.transport.getState()
      // A reset's snapshot must include the reset itself.
      for (let i = 0; i < 5 && state.last_event_id < minEventId; i++) {
        await sleep(300)
        state = await this.transport.getState()
      }
      if (gen !== this.generation) return
      const history = state.last_event_id > 0 ? await this.loadHistory(state.last_event_id, !!state.warning_area) : []
      if (gen !== this.generation) return
      this.store.loadSnapshot(state, history)
      this.openStream(gen)
    } catch (err) {
      if (gen !== this.generation) return
      this.store.setConn('offline', describe(err))
      this.retryLater(() => void this.resync(minEventId))
    }
  }

  /**
   * Recent events up to `lastId`, for the feed. While a warning is active, keep paging back
   * until the run's alert.received (or the reset before it) so the alert time and plan show
   * after a reload, even late in a long run.
   */
  private async loadHistory(lastId: number, warningActive: boolean): Promise<RunEvent[]> {
    const page = async (after: number, upTo: number): Promise<RunEvent[] | null> => {
      try {
        const res = await this.transport.getEvents(after, upTo - after)
        return res.events.filter((e) => e.id <= upTo)
      } catch {
        return null
      }
    }
    let low = Math.max(0, lastId - HISTORY_WINDOW)
    let history = (await page(low, lastId)) ?? []
    const hasRunStart = () => history.some((e) => e.type === 'alert.received' || e.type === 'run.reset')
    for (let i = 0; warningActive && low > 0 && i < MAX_HISTORY_PAGES && !hasRunStart(); i++) {
      const next = Math.max(0, low - HISTORY_WINDOW)
      const older = await page(next, low)
      if (!older) break
      history = older.concat(history)
      low = next
    }
    return history
  }

  private openStream(gen: number): void {
    this.stream = this.transport.openStream(this.store.lastEventId, {
      onOpen: () => {
        if (gen !== this.generation) return
        this.attempts = 0
        this.store.setConn('live')
      },
      onEvent: (event) => {
        if (gen !== this.generation) return
        const fresh = this.store.applyEvent(event)
        if (fresh && event.type === 'run.reset') void this.resync(event.id)
      },
      onClose: () => {
        if (gen !== this.generation) return
        this.stream = null
        this.store.setConn('reconnecting')
        this.retryLater(() => void this.reconnect(gen))
      },
    })
  }

  /** After a drop, resume from the last event seen, unless the backend's log started over. */
  private async reconnect(gen: number): Promise<void> {
    try {
      const health = await this.transport.getHealth()
      if (gen !== this.generation) return
      if (health.last_event_id < this.store.lastEventId) {
        void this.resync()
        return
      }
      this.openStream(gen)
    } catch (err) {
      if (gen !== this.generation) return
      this.store.setConn('offline', describe(err))
      this.retryLater(() => void this.reconnect(gen))
    }
  }

  private retryLater(fn: () => void): void {
    if (!this.running) return
    this.clearRetry()
    const delay = Math.min(MAX_RETRY_MS, 500 * 2 ** this.attempts++)
    this.retryTimer = setTimeout(fn, delay)
  }

  private clearRetry(): void {
    if (this.retryTimer != null) clearTimeout(this.retryTimer)
    this.retryTimer = null
  }
}
