import { useEffect, useState, type ReactNode } from 'react'
import { ApiError } from '../api/transport'
import type { HouseholdCall, HouseholdDetail as Detail } from '../api/types'
import { StatusDot } from '../components/StatusDot'
import { formatTime, formatWhen, zoneAbbrev } from '../lib/format'
import { actionLabel, channelLabel, fmtMoney, languageLabel, titleCase } from '../lib/labels'
import { store, transport, useRunSnapshot } from '../live'

/** Refetch at most this often while events for the open household stream in. */
const REFETCH_MS = 1200
const LADDER_STEPS = 4

interface DetailState {
  data: Detail | null
  loading: boolean
  error: string | null
}

/** GET /households/{id}, refetched (throttled) whenever an event for that household arrives. */
function useHouseholdDetail(id: number): DetailState {
  const [state, setState] = useState<DetailState>({ data: null, loading: true, error: null })

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false
    let again = false
    let last = 0
    setState({ data: null, loading: true, error: null })

    async function fetchNow() {
      if (inFlight) {
        again = true
        return
      }
      inFlight = true
      last = performance.now()
      try {
        const data = await transport.getHousehold(id)
        if (alive) setState({ data, loading: false, error: null })
      } catch (err) {
        if (!alive) return
        const msg =
          err instanceof ApiError && err.status === 404
            ? 'Details are not available from the backend yet.'
            : err instanceof Error
              ? err.message
              : String(err)
        setState((s) => ({ data: s.data, loading: false, error: msg }))
      } finally {
        inFlight = false
        if (alive && again) {
          again = false
          schedule()
        }
      }
    }

    function schedule() {
      if (timer != null) return
      const wait = Math.max(0, REFETCH_MS - (performance.now() - last))
      timer = setTimeout(() => {
        timer = null
        void fetchNow()
      }, wait)
    }

    void fetchNow()
    const off = store.onEvent((e) => {
      if (e.household_id === id || e.type === 'run.reset') schedule()
    })
    return () => {
      alive = false
      off()
      if (timer != null) clearTimeout(timer)
    }
  }, [id])

  return state
}

/**
 * M8: everything about one household, opened by clicking a dot (store.select) or a row in the
 * approvals / escalations lists. Floats over the left of the map; Esc or X closes it.
 */
export function HouseholdDetailPanel() {
  const snap = useRunSnapshot()
  const id = snap.selectedId
  useEffect(() => {
    if (id == null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') store.deselect(id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id])
  if (id == null) return null
  return <DetailCard key={id} id={id} />
}

function DetailCard({ id }: { id: number }) {
  useRunSnapshot() // live status color from the stream between refetches
  const { data, loading, error } = useHouseholdDetail(id)
  const base = store.households.get(id)
  const tz = store.meta?.display_tz ?? 'America/Chicago'
  const name = data?.name ?? base?.name ?? `Household ${id}`
  const address = data?.address ?? base?.address ?? ''
  // The stream is newer than the last fetch, so its status wins.
  const status = store.statusOf(base?.status ?? data?.status ?? 'not_called')
  const risk = data?.risk_score ?? base?.risk_score
  const wave = data?.wave ?? base?.wave

  return (
    <aside
      className="absolute top-3 bottom-3 left-3 z-[900] flex w-[min(420px,calc(100%-24px))] flex-col overflow-hidden rounded-xl border border-line bg-panel/97 shadow-2xl backdrop-blur"
      aria-label={`Household details: ${name}`}
    >
      <header className="flex items-start gap-3 border-b border-line px-5 pt-4 pb-3.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[18px] font-semibold leading-snug text-ink">{name}</div>
          <div className="mt-0.5 text-[14px] text-muted">{address}</div>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[14px]">
            <span className="flex items-center gap-2 font-semibold" style={{ color: status.color }}>
              <StatusDot status={status} ringColor={store.ringColor} size={11} />
              <span className="text-ink">{status.label}</span>
            </span>
            {risk != null && (
              <span className="text-muted">
                Risk <span className="font-semibold tabular-nums text-ink">{Math.round(risk)}</span>
                <span className="text-faint"> / 100</span>
              </span>
            )}
            {wave != null && (
              <span className="text-muted">
                Wave <span className="font-semibold tabular-nums text-ink">{wave}</span>
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={() => store.deselect(id)}
          aria-label="Close"
          title="Close (Esc)"
          className="-mt-1 -mr-2 grid h-9 w-9 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-raised hover:text-ink"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5">
        {!data && (
          <p className="pt-4 text-[13.5px] text-faint">{loading ? 'Loading details…' : (error ?? 'No details.')}</p>
        )}
        {data && (
          <>
            {error && <p className="pt-3 text-[12px] text-faint">Couldn't refresh: {error}</p>}
            <NextFollowUp detail={data} tz={tz} />
            <Section title="Why this risk score">
              {data.risk_factors.length ? <RiskBreakdown detail={data} /> : <Muted>No risk factors.</Muted>}
            </Section>
            <Section title="Profile">
              <Profile detail={data} />
            </Section>
            <Section title={`Needs${data.needs.length ? ` · ${data.needs.length}` : ''}`}>
              {data.needs.length ? (
                <ul className="space-y-2">
                  {data.needs.map((n) => {
                    const u = store.meta?.urgencies.find((x) => x.value === n.urgency)
                    return (
                      <li key={n.id} className="flex gap-2.5 text-[14px] leading-snug">
                        <span className="mt-[6px]">
                          <StatusDot status={{ color: u?.color ?? '#9ca3af', style: 'solid' }} ringColor={store.ringColor} size={8} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="font-medium text-ink">{titleCase(n.category)}</span>
                          <span className="text-muted"> · {n.urgency}{u ? ` ${u.label}` : ''}</span>
                          <span className="block text-ink/90">{n.details}</span>
                        </span>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <Muted>None recorded.</Muted>
              )}
            </Section>
            <Section title={`Calls${data.calls.length ? ` · ${data.calls.length}` : ''}`}>
              {data.calls.length ? (
                <ul className="space-y-2.5">
                  {[...data.calls].reverse().map((c) => (
                    <CallRow key={c.id} call={c} tz={tz} />
                  ))}
                </ul>
              ) : (
                <Muted>Not called yet.</Muted>
              )}
            </Section>
            <Section title={`Actions${data.actions.length ? ` · ${data.actions.length}` : ''}`}>
              {data.actions.length ? (
                <ul className="space-y-2">
                  {data.actions.map((a) => (
                    <li key={a.id} className="rounded-lg border border-line px-3 py-2 text-[14px]">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="font-semibold text-ink">{actionLabel(a.type)}</span>
                        <ActionStatus status={a.status} needsApproval={a.needs_approval} />
                      </div>
                      <div className="mt-0.5 text-[13px] text-muted">
                        {[a.resource, a.cost ? fmtMoney(a.cost) : null, a.scheduled_for ? `for ${formatWhen(a.scheduled_for, tz, store.getSnapshot().runTime)}` : null]
                          .filter(Boolean)
                          .join(' · ') || 'No resource yet'}
                      </div>
                      {a.reason && <div className="mt-0.5 text-[13px] leading-snug text-ink/85">{a.reason}</div>}
                    </li>
                  ))}
                </ul>
              ) : (
                <Muted>No actions.</Muted>
              )}
            </Section>
          </>
        )}
      </div>
    </aside>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pt-4">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">{title}</h3>
      {children}
    </section>
  )
}

const Muted = ({ children }: { children: ReactNode }) => <p className="text-[13.5px] text-faint">{children}</p>

function NextFollowUp({ detail, tz }: { detail: Detail; tz: string }) {
  const f = detail.next_followup
  if (!f) return null
  const zone = zoneAbbrev(tz, f.at)
  return (
    <div className="mt-4 flex items-center gap-3 rounded-lg bg-raised px-3.5 py-2.5">
      <div className="flex gap-1" aria-label={`Step ${f.step} of ${LADDER_STEPS}`}>
        {Array.from({ length: LADDER_STEPS }, (_, i) => (
          <span key={i} className="h-2 w-5 rounded-full" style={{ background: i < f.step ? 'var(--accent)' : 'var(--line)' }} />
        ))}
      </div>
      <div className="text-[14px] leading-snug">
        <div className="font-semibold text-ink">
          Next: {channelLabel(f.channel)} at {formatWhen(f.at, tz, store.getSnapshot().runTime)}
          {zone && <span className="font-normal text-faint"> {zone}</span>}
        </div>
        <div className="text-[12.5px] text-muted">Follow-up step {f.step} of {LADDER_STEPS}</div>
      </div>
    </div>
  )
}

function RiskBreakdown({ detail }: { detail: Detail }) {
  const max = Math.max(...detail.risk_factors.map((f) => f.points), 1)
  return (
    <ul className="space-y-1.5">
      {[...detail.risk_factors]
        .sort((a, b) => b.points - a.points)
        .map((f) => (
          <li key={f.key} className="grid grid-cols-[1fr_auto] items-center gap-x-3 text-[13.5px]">
            <span className="truncate text-ink">{f.label}</span>
            <span className="tabular-nums font-semibold text-ink">+{Math.round(f.points)}</span>
            <span className="col-span-2 mt-0.5 h-1.5 overflow-hidden rounded-full bg-raised">
              <span className="block h-full rounded-full bg-accent/80" style={{ width: `${(f.points / max) * 100}%` }} />
            </span>
          </li>
        ))}
    </ul>
  )
}

function Profile({ detail }: { detail: Detail }) {
  const p = detail.profile
  const yesNo = (v: boolean | null | undefined) => (v == null ? '—' : v ? 'Yes' : 'No')
  const rows: [string, ReactNode][] = [
    ['Age', p.age ?? '—'],
    ['Lives alone', yesNo(p.lives_alone)],
    ['Medical device', p.medical_device ?? 'None'],
    ['Mobility', p.mobility ?? 'No limits noted'],
    ['Car', yesNo(p.has_car)],
    ['Kids under 5', p.kids_under_5 ?? 0],
    ['Language', languageLabel(p.language)],
  ]
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-1 text-[14px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="font-medium text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function CallRow({ call, tz }: { call: HouseholdCall; tz: string }) {
  const lines = call.transcript ?? []
  return (
    <li className="rounded-lg border border-line px-3 py-2">
      <div className="flex items-baseline justify-between gap-3 text-[14px]">
        <span className="font-semibold text-ink">
          Attempt {call.attempt} · {channelLabel(call.channel)}
        </span>
        <span className="text-[12.5px] tabular-nums text-faint">{formatTime(call.started_at, tz, false)}</span>
      </div>
      <div className="mt-0.5 text-[13px] font-medium text-muted">{call.outcome ? titleCase(call.outcome) : 'In progress'}</div>
      {call.summary && <p className="mt-1 text-[13.5px] leading-snug text-ink/90">{call.summary}</p>}
      {lines.length > 0 && (
        <details className="group mt-1.5">
          <summary className="cursor-pointer select-none text-[12.5px] font-medium text-accent">
            Transcript · {lines.length} lines
          </summary>
          <ol className="mt-1.5 space-y-1 border-l-2 border-line pl-3">
            {lines.map((l, i) => (
              <li key={i} className="text-[13px] leading-snug">
                <span className="font-semibold text-muted">{l.role === 'agent' ? 'Agent' : l.role === 'resident' ? 'Resident' : titleCase(l.role)}: </span>
                <span className="text-ink/90">{l.line ?? (l as { text?: string }).text}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </li>
  )
}

function ActionStatus({ status, needsApproval }: { status: string; needsApproval: boolean }) {
  const waiting = status === 'pending_approval' || (needsApproval && status === 'pending')
  return (
    <span
      className={`shrink-0 rounded px-1.5 py-[1px] text-[11px] font-semibold uppercase tracking-[0.08em] ${
        waiting ? 'bg-accent text-accent-ink' : 'bg-raised text-muted'
      }`}
    >
      {waiting ? 'Waiting for approval' : titleCase(status)}
    </span>
  )
}
