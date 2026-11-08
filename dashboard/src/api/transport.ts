import type { ApprovalItem, ApprovalsList, EventsPage, Health, HouseholdDetail, Meta, Results, RunEvent, RunState } from './types'

/** Where the backend runs. Set VITE_API_BASE (see .env.example); no trailing slash. */
export const API_BASE = (
  (import.meta.env.VITE_API_BASE as string | undefined)?.trim() || 'http://127.0.0.1:8100'
).replace(/\/+$/, '')

export interface StreamHandlers {
  onOpen(): void
  onEvent(event: RunEvent): void
  /** Called once when the stream drops on its own (not when we close it). */
  onClose(info: { code: number; reason: string }): void
}

export interface StreamHandle {
  close(): void
}

/**
 * Everything the dashboard needs from the backend. The HTTP version talks to FastAPI; the
 * fixture version (src/fixture) fakes a run in the browser for UI work without a backend.
 */
export interface Transport {
  readonly label: string
  getMeta(): Promise<Meta>
  getState(): Promise<RunState>
  getEvents(since: number, limit: number): Promise<EventsPage>
  getHealth(): Promise<Health>
  startDemo(speed: number): Promise<unknown>
  reset(): Promise<unknown>
  openStream(since: number, handlers: StreamHandlers): StreamHandle
  // M8
  getApprovals(): Promise<ApprovalsList>
  /** 404 unknown, 409 already decided (both as ApiError). */
  decideApproval(actionId: number, approve: boolean): Promise<ApprovalItem>
  getHousehold(id: number): Promise<HouseholdDetail>
  // M9
  /** The latest evaluation; 404 (ApiError) until `scripts.run_eval` has written one. */
  getResults(): Promise<Results>
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function httpTransport(base: string = API_BASE): Transport {
  async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    let res: Response
    try {
      const headers: Record<string, string> = { Accept: 'application/json' }
      if (body !== undefined) headers['Content-Type'] = 'application/json'
      res = await fetch(base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    } catch {
      throw new ApiError(0, `Can't reach the backend at ${base}`)
    }
    if (!res.ok) {
      let detail = ''
      try {
        const body = await res.json()
        detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body)
      } catch {
        /* no JSON body */
      }
      throw new ApiError(res.status, `${method} ${path} failed (${res.status})${detail ? `: ${detail}` : ''}`)
    }
    return (await res.json()) as T
  }

  return {
    label: base,
    getMeta: () => request<Meta>('GET', '/meta'),
    getState: () => request<RunState>('GET', '/state'),
    getEvents: (since, limit) => request<EventsPage>('GET', `/events?since=${since}&limit=${limit}`),
    getHealth: () => request<Health>('GET', '/health'),
    startDemo: (speed) => request('POST', `/alerts/demo?speed=${encodeURIComponent(speed)}`),
    reset: () => request('POST', '/reset'),
    getApprovals: () => request<ApprovalsList>('GET', '/approvals'),
    decideApproval: (actionId, approve) =>
      request<ApprovalItem>('POST', `/approvals/${actionId}/${approve ? 'approve' : 'reject'}`, { by: 'coordinator' }),
    getHousehold: (id) => request<HouseholdDetail>('GET', `/households/${id}`),
    getResults: () => request<Results>('GET', '/results'),
    openStream(since, handlers) {
      const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws?since=${since}`)
      let done = false
      ws.onopen = () => handlers.onOpen()
      ws.onmessage = (message) => {
        let event: RunEvent
        try {
          event = JSON.parse(message.data as string) as RunEvent
        } catch {
          console.warn('[rollcall] ignoring a malformed event', message.data)
          return
        }
        handlers.onEvent(event)
      }
      ws.onclose = (ev) => {
        if (done) return
        done = true
        handlers.onClose({ code: ev.code, reason: ev.reason })
      }
      return {
        close() {
          done = true
          ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null
          try {
            ws.close()
          } catch {
            /* already closed */
          }
        },
      }
    },
  }
}
