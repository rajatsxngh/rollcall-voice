import type { ReactNode } from 'react'
import type { StatusMeta } from '../api/types'
import { fmtInt, percent } from '../lib/format'
import { store, useRunSnapshot } from '../live'
import { Panel } from './Panel'

/** The five counters from docs/API.md, with "not reached yet" as the headline. */
export function CountersPanel() {
  const snap = useRunSnapshot()
  const { counts, meta, plan } = snap
  const active = snap.warningArea != null
  const emergency = snap.statusCounts.emergency ?? 0

  return (
    <Panel
      title="Response"
      aside={active ? `${fmtInt(counts.affected)} homes in the warning area` : `${fmtInt(counts.households)} homes on the map`}
    >
      <div className="px-5 pb-5 short:pb-4">
        <div className="flex items-end justify-between gap-4">
          <div>
            <div className="text-[13px] font-medium text-muted">Not reached yet</div>
            <div
              className={`mt-1 text-[64px] font-semibold leading-[0.95] tracking-tight tabular-nums short:text-[50px] ${active ? 'text-ink' : 'text-faint'}`}
            >
              {active ? fmtInt(counts.not_reached) : '—'}
            </div>
          </div>
          <div className="pb-1 text-right text-[13px] leading-snug text-muted">
            {active ? (
              <>
                of {fmtInt(counts.affected)} affected
                <br />
                <span className="font-medium text-ink">{percent(counts.affected - counts.not_reached, counts.affected)}</span>{' '}
                reached or escalated
              </>
            ) : (
              <>
                Starts when a
                <br />
                warning comes in
              </>
            )}
          </div>
        </div>

        {meta && <StatusBar statuses={meta.statuses} counts={snap.statusCounts} visible={active} />}

        <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line short:mt-3.5">
          <Stat label="Called" value={counts.called} sub={active ? `${percent(counts.called, counts.affected)} of affected` : 'phone calls made'} />
          <Stat label="Reached" value={counts.reached} sub={active ? `${percent(counts.reached, counts.affected)} of affected` : 'someone answered'} />
          <Stat
            label="Urgent"
            value={counts.urgent}
            keyDots={<UrgentKey />}
            sub={counts.urgent ? `${fmtInt(emergency)} emergency` : 'urgent or emergency'}
          />
          <Stat label="Help sent" value={counts.help_sent} sub="help on the way" />
        </div>

        {plan?.summary && (
          <p className="mt-4 text-[13px] leading-snug text-muted short:mt-3">
            <span className="font-semibold text-ink">Plan</span> · {plan.summary}
          </p>
        )}
      </div>
    </Panel>
  )
}

function Stat({ label, value, sub, keyDots }: { label: string; value: number; sub: string; keyDots?: ReactNode }) {
  return (
    <div className="bg-panel px-4 pt-3 pb-3.5 short:pt-2 short:pb-2.5">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
        {label}
        {keyDots}
      </div>
      <div className="mt-1.5 text-[40px] font-semibold leading-none tracking-tight tabular-nums text-ink short:mt-1 short:text-[32px]">{fmtInt(value)}</div>
      <div className="mt-1.5 truncate text-[12.5px] text-faint short:mt-1">{sub}</div>
    </div>
  )
}

/** The two colors the Urgent counter counts, as on the map. */
function UrgentKey() {
  const urgent = store.statusByValue.get('urgent')
  const emergency = store.statusByValue.get('emergency')
  if (!urgent || !emergency) return null
  return (
    <span className="flex gap-1" aria-hidden>
      <span className="h-2 w-2 rounded-full" style={{ background: urgent.color }} />
      <span className="h-2 w-2 rounded-full" style={{ background: emergency.color }} />
    </span>
  )
}

/**
 * Homes in the warning area by status, as one bar: reached (meta order), then escalated, then
 * still waiting (no answer, calling, not called). It fills from the left as the run goes.
 */
function StatusBar({ statuses, counts, visible }: { statuses: StatusMeta[]; counts: Record<string, number>; visible: boolean }) {
  const group = (s: StatusMeta) => (s.reached ? 0 : s.covered ? 1 : 2)
  const ordered = [
    ...statuses.filter((s) => group(s) === 0),
    ...statuses.filter((s) => group(s) === 1),
    ...statuses.filter((s) => group(s) === 2).reverse(),
  ]
  const total = ordered.reduce((n, s) => n + (counts[s.value] ?? 0), 0)
  return (
    <div className="mt-4 flex h-3 gap-[2px] overflow-hidden rounded-full bg-raised short:mt-3" aria-label="Homes by status">
      {visible &&
        total > 0 &&
        ordered.map((s) => {
          const n = counts[s.value] ?? 0
          if (!n) return null
          return (
            <div
              key={s.value}
              title={`${s.label}: ${fmtInt(n)}`}
              className={`h-full min-w-[3px] transition-[flex-grow] duration-300 ease-out ${s.style === 'pulse' ? 'rc-breathe' : ''}`}
              style={{ flexGrow: n, flexBasis: 0, ...segmentStyle(s) }}
            />
          )
        })}
    </div>
  )
}

function segmentStyle(s: StatusMeta) {
  switch (s.style) {
    case 'hollow':
      return { boxShadow: `inset 0 0 0 1.5px ${s.color}` }
    case 'ring':
      return { background: s.color, boxShadow: `inset 0 0 0 2px ${store.ringColor}` }
    default:
      // Not called is the empty part of the bar.
      return { background: s.value === 'not_called' ? `color-mix(in srgb, ${s.color} 32%, transparent)` : s.color }
  }
}
