import { useMemo, useState } from 'react'
import { ApiError } from '../api/transport'
import type { ApprovalItem, Household } from '../api/types'
import { StatusDot } from '../components/StatusDot'
import { fmtInt, formatTime } from '../lib/format'
import { actionLabel, fmtMoney } from '../lib/labels'
import { usePersistentState } from '../lib/usePersistentState'
import { approvals, store, transport, useApprovals, useRunSnapshot } from '../live'
import { isLifeSafety } from '../store/approvals'

type Tab = 'approvals' | 'escalations'

/**
 * M8: what waits on the coordinator. Two tabs between the counters and the feed:
 * the approvals queue (one-click approve / reject) and the households escalated to an
 * in-person check. Capped in height so the feed keeps its room; collapsible.
 */
export function QueuePanel() {
  const snap = useRunSnapshot()
  const queue = useApprovals()
  const [tab, setTab] = usePersistentState<Tab>('rc.queueTab', 'approvals')
  const [collapsed, setCollapsed] = usePersistentState<boolean>('rc.queueCollapsed', false)

  // Recomputed per flush (at most every 80 ms); 2,000 homes is cheap.
  const escalated = useMemo(() => {
    const list: Household[] = []
    for (const h of store.households.values()) if (h.status === 'escalated') list.push(h)
    return list.sort((a, b) => b.risk_score - a.risk_score)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snap])

  const pending = queue.items.length
  const lifeSafety = queue.items.filter(isLifeSafety).length

  return (
    <section className={`flex min-h-0 shrink-0 flex-col border-b border-line ${collapsed ? '' : 'max-h-[40%] short:max-h-[34%]'}`}>
      <header className="flex items-center gap-1 px-3 pt-2.5 pb-1.5" role="tablist">
        <TabButton active={tab === 'approvals'} onClick={() => { setTab('approvals'); setCollapsed(false) }}>
          Approvals
          <Badge n={pending} tone={lifeSafety ? 'alert' : pending ? 'accent' : 'none'} />
        </TabButton>
        <TabButton active={tab === 'escalations'} onClick={() => { setTab('escalations'); setCollapsed(false) }}>
          Escalations
          <Badge n={escalated.length} tone={escalated.length ? 'ring' : 'none'} />
        </TabButton>
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand' : 'Collapse'}
          title={collapsed ? 'Expand' : 'Collapse'}
          className="ml-auto grid h-8 w-8 place-items-center rounded-md text-faint transition hover:bg-raised hover:text-ink"
        >
          <Chevron up={!collapsed} />
        </button>
      </header>
      {!collapsed && (
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-1">
          {tab === 'approvals' ? <ApprovalsList items={queue.items} loaded={queue.loaded} error={queue.error} /> : <EscalationsList homes={escalated} />}
        </div>
      )}
    </section>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex h-8 items-center gap-2 rounded-md px-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] transition ${
        active ? 'bg-raised text-ink' : 'text-muted hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

function Badge({ n, tone }: { n: number; tone: 'alert' | 'accent' | 'ring' | 'none' }) {
  const color = tone === 'alert' ? store.ringColor : tone === 'accent' ? 'var(--accent)' : tone === 'ring' ? 'var(--muted)' : null
  return (
    <span
      className="min-w-[22px] rounded-full px-1.5 py-[1px] text-center text-[12px] font-semibold tracking-normal tabular-nums"
      style={color ? { background: color, color: tone === 'ring' ? 'var(--panel)' : '#fff' } : { color: 'var(--faint)' }}
    >
      {fmtInt(n)}
    </span>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-5 py-2.5 text-[13.5px] leading-snug text-faint">{children}</div>
}

function ApprovalsList({ items, loaded, error }: { items: ApprovalItem[]; loaded: boolean; error: string | null }) {
  if (!items.length) {
    if (error && !loaded) return <Empty>Approvals aren't available from the backend yet.</Empty>
    return <Empty>Nothing waiting. Generators, hotel vouchers and costly help wait here for a yes.</Empty>
  }
  return (
    <ul>
      {items.map((item) => (
        <ApprovalRow key={item.action_id} item={item} />
      ))}
    </ul>
  )
}

function ApprovalRow({ item }: { item: ApprovalItem }) {
  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [error, setError] = useState<string | null>(null)
  const urgent = isLifeSafety(item)
  const tz = store.meta?.display_tz ?? 'America/Chicago'
  const status = item.urgency_status ? store.statusOf(item.urgency_status) : null

  async function decide(approve: boolean) {
    setBusy(approve ? 'approve' : 'reject')
    setError(null)
    try {
      await transport.decideApproval(item.action_id, approve)
      approvals.remove(item.action_id)
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        // Someone (or the engine) already decided it: show the queue as it is now.
        await approvals.load()
        return
      }
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <li
      className="border-b border-line/60 px-5 py-2.5 last:border-b-0"
      style={urgent ? { boxShadow: `inset 3px 0 0 ${store.ringColor}` } : undefined}
    >
      <div className="flex items-start gap-3">
        <button type="button" onClick={() => store.select(item.household_id)} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="text-[15px] font-semibold text-ink">{actionLabel(item.type)}</span>
            <span className="text-[14px] font-medium tabular-nums text-muted">{fmtMoney(item.cost)}</span>
            {urgent && (
              <span
                className="rounded px-1.5 py-[1px] text-[10px] font-bold uppercase tracking-[0.1em] text-white"
                style={{ background: store.ringColor }}
              >
                Life safety
              </span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 truncate text-[13px] text-ink/90">
            {status && <StatusDot status={status} ringColor={store.ringColor} size={8} />}
            <span className="truncate hover:underline">{item.address || item.name}</span>
          </div>
          {item.reason && <div className="mt-0.5 text-[13px] leading-snug text-muted">{item.reason}</div>}
          <div className="mt-0.5 text-[11.5px] tabular-nums text-faint">Asked {formatTime(item.created_at, tz, false)}</div>
          {error && <div className="mt-1 text-[12px] text-[color:var(--ink)]/80">{error}</div>}
        </button>
        <div className="flex shrink-0 flex-col gap-1.5 pt-0.5">
          <button
            type="button"
            disabled={busy != null}
            onClick={() => decide(true)}
            className="h-8 rounded-md bg-accent px-3 text-[13px] font-semibold text-accent-ink transition hover:brightness-110 disabled:opacity-50"
          >
            {busy === 'approve' ? '…' : 'Approve'}
          </button>
          <button
            type="button"
            disabled={busy != null}
            onClick={() => decide(false)}
            className="h-8 rounded-md border border-line px-3 text-[13px] font-medium text-ink transition hover:bg-raised disabled:opacity-50"
          >
            {busy === 'reject' ? '…' : 'Reject'}
          </button>
        </div>
      </div>
    </li>
  )
}

function EscalationsList({ homes }: { homes: Household[] }) {
  if (!homes.length) return <Empty>No escalations. Homes nobody reaches after a call, a text and a last call get an in-person check and show up here.</Empty>
  const s = store.statusOf('escalated')
  return (
    <ul>
      {homes.map((h) => (
        <li key={h.id} className="border-b border-line/60 last:border-b-0">
          <button
            type="button"
            onClick={() => store.select(h.id)}
            className="flex w-full items-center gap-3 px-5 py-2 text-left transition hover:bg-raised"
          >
            <StatusDot status={s} ringColor={store.ringColor} size={9} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[14px] font-medium text-ink">{h.address}</span>
              <span className="block truncate text-[12.5px] text-muted">
                {h.name} · risk {Math.round(h.risk_score)}
              </span>
            </span>
            <Chevron right />
          </button>
        </li>
      ))}
    </ul>
  )
}

function Chevron({ up, right }: { up?: boolean; right?: boolean }) {
  const rotate = right ? -90 : up ? 180 : 0
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden className="shrink-0 text-faint" style={{ transform: `rotate(${rotate}deg)` }}>
      <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
