import { useEffect, useState } from 'react'
import { formatElapsed, formatTime, zoneAbbrev } from '../lib/format'
import type { Theme } from '../lib/theme'
import { usePersistentState } from '../lib/usePersistentState'
import { transport, usingFixture, useRunSnapshot } from '../live'
import type { AlertInfo, ConnState } from '../store/runStore'

const SPEEDS = [1, 10, 30, 60]

export function TopBar({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const snap = useRunSnapshot()
  const [speed, setSpeed] = usePersistentState<number>('rc.speed', 10)
  const [busy, setBusy] = useState<null | 'alert' | 'reset'>(null)
  const [error, setError] = useState<string | null>(null)
  const active = snap.warningArea != null
  const tz = snap.meta?.display_tz ?? 'America/Chicago'

  useEffect(() => {
    if (!error) return
    const t = setTimeout(() => setError(null), 8000)
    return () => clearTimeout(t)
  }, [error])

  async function run(kind: 'alert' | 'reset', action: () => Promise<unknown>) {
    setBusy(kind)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <header className="flex h-[68px] shrink-0 items-center gap-4 whitespace-nowrap border-b border-line bg-panel px-5 xl:gap-6">
      <Brand fixture={usingFixture} />
      <div className="h-8 w-px shrink-0 bg-line" />
      <AlertStatus alert={snap.alert} active={active} runTime={snap.runTime} tz={tz} />

      <div className="ml-auto flex shrink-0 items-center gap-3">
        {error && (
          <span className="max-w-[260px] truncate text-[13px] text-muted" title={error}>
            {error}
          </span>
        )}
        {/* Speed is fixed once a run starts, so the picker steps aside while a warning is active. */}
        {!active && <SpeedPicker value={speed} onChange={setSpeed} disabled={busy != null} />}
        <button
          type="button"
          onClick={() => run('alert', () => transport.startDemo(speed))}
          disabled={!snap.ready || active || busy != null}
          className="flex h-10 items-center gap-2 rounded-lg bg-accent px-4 text-[14px] font-semibold text-accent-ink shadow-sm transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <WarningIcon />
          {busy === 'alert' ? 'Starting…' : active ? 'Warning active' : 'Tornado warning'}
        </button>
        <button
          type="button"
          onClick={() => run('reset', () => transport.reset())}
          disabled={!snap.ready || busy != null}
          className="flex h-10 items-center gap-2 rounded-lg border border-line bg-panel px-3.5 text-[14px] font-medium text-ink transition hover:bg-raised disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ResetIcon />
          {busy === 'reset' ? 'Resetting…' : 'Reset'}
        </button>
        <div className="mx-1 h-8 w-px bg-line" />
        <Connection conn={snap.conn} error={snap.error} />
        <button
          type="button"
          onClick={onToggleTheme}
          title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
          aria-label="Toggle theme"
          className="grid h-9 w-9 place-items-center rounded-lg text-muted transition hover:bg-raised hover:text-ink"
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </header>
  )
}

function Brand({ fixture }: { fixture: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-3">
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
        <circle cx="16" cy="16" r="11" fill="none" stroke="var(--accent)" strokeWidth="2.5" opacity="0.5" />
        <circle cx="16" cy="16" r="5" fill="var(--accent)" />
      </svg>
      <div className="leading-tight">
        <div className="text-[17px] font-semibold tracking-tight text-ink">
          RollCall <span className="font-normal text-muted">Voice</span>
        </div>
        <div className="text-[12px] text-faint">
          Richardson, TX{fixture && <span className="ml-2 font-semibold uppercase tracking-wider text-accent">fixture data</span>}
        </div>
      </div>
    </div>
  )
}

function AlertStatus({ alert, active, runTime, tz }: { alert: AlertInfo | null; active: boolean; runTime: string | null; tz: string }) {
  if (!active || !alert) {
    return (
      <div className="flex min-w-0 items-center gap-2.5 text-[14px] text-muted">
        <span className="h-2.5 w-2.5 shrink-0 rounded-full border-2 border-faint" aria-hidden />
        <span className="truncate">No active warning</span>
      </div>
    )
  }
  const since = alert.time ? formatTime(alert.time, tz, false) : null
  const elapsed = formatElapsed(alert.time, runTime)
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className="flex shrink-0 items-center gap-2 rounded-md border border-accent/45 bg-accent/15 px-2.5 py-1 text-[12.5px] font-bold uppercase tracking-[0.12em] text-accent">
        <span className="rc-breathe h-2 w-2 rounded-full bg-accent" aria-hidden />
        {alert.title}
      </span>
      {since && (
        <span className="hidden truncate text-[14px] text-muted lg:inline">
          since <span className="tabular-nums text-ink">{since}</span> {zoneAbbrev(tz, alert.time)}
          {elapsed != null && (
            <>
              {' · '}
              <span className="tabular-nums text-ink">+{elapsed}</span>
            </>
          )}
        </span>
      )}
    </div>
  )
}

function SpeedPicker({ value, onChange, disabled }: { value: number; onChange: (v: number) => void; disabled: boolean }) {
  return (
    <div
      role="radiogroup"
      aria-label="Clock speed"
      title="Clock speed for the next run"
      className={`flex h-10 items-center rounded-lg border border-line bg-bg p-1 ${disabled ? 'opacity-50' : ''}`}
    >
      <span className="hidden px-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-faint xl:block">Speed</span>
      {SPEEDS.map((s) => (
        <button
          key={s}
          type="button"
          role="radio"
          aria-checked={s === value}
          disabled={disabled}
          onClick={() => onChange(s)}
          className={`h-full rounded-md px-2.5 text-[13px] font-semibold tabular-nums transition disabled:cursor-not-allowed ${
            s === value ? 'bg-raised text-ink shadow-sm' : 'text-muted hover:text-ink'
          }`}
        >
          {s}×
        </button>
      ))}
    </div>
  )
}

const CONN_LABEL: Record<ConnState, string> = {
  live: 'Live',
  connecting: 'Connecting',
  reconnecting: 'Reconnecting',
  offline: 'Offline',
}

function Connection({ conn, error }: { conn: ConnState; error: string | null }) {
  const live = conn === 'live'
  return (
    <div className="flex items-center gap-2 text-[13px]" title={error ?? `Events from ${transport.label}`}>
      <span className="relative grid h-2.5 w-2.5 place-items-center" aria-hidden>
        {live && <span className="rc-ping absolute inset-0 rounded-full border border-accent" />}
        <span className={`h-2.5 w-2.5 rounded-full ${live ? 'bg-accent' : 'border-2 border-faint'}`} />
      </span>
      <span className={live ? 'font-medium text-ink' : 'text-muted'}>{CONN_LABEL[conn]}</span>
    </div>
  )
}

function WarningIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
      <path d="M3 5h18M5 9.5h14M8 14h9M10.5 18.5h5M13 22h1" />
    </svg>
  )
}

function ResetIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <path d="M3 4v5h5" />
    </svg>
  )
}

function SunIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  )
}
