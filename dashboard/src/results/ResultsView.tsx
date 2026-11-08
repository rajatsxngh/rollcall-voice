import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ApiError } from '../api/transport'
import type { Results, ResultsRun, TrapCase, TrapResult } from '../api/types'
import { fmtInt, formatTime, zoneAbbrev } from '../lib/format'
import { transport, usingFixture } from '../live'
import { CurveChart, CurveLegend } from './CurveChart'
import {
  callsLabel,
  formatAllUrgent,
  formatMinutes,
  formatPct,
  formatSpeedup,
  personaLabel,
  speedupFor,
  splitVerdict,
  triageLabel,
  urgencyRank,
  type SpeedupKey,
} from './model'

const TZ = 'America/Chicago'

type Load = { kind: 'loading' } | { kind: 'missing' } | { kind: 'error'; message: string } | { kind: 'ready'; results: Results }

/** M9 test mode: RollCall (risk-ranked) vs list order, from GET /results. */
export function ResultsView() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' })

  const fetchResults = useCallback(async () => {
    setLoad((l) => (l.kind === 'ready' ? l : { kind: 'loading' }))
    try {
      setLoad({ kind: 'ready', results: await transport.getResults() })
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setLoad({ kind: 'missing' })
      else setLoad({ kind: 'error', message: err instanceof Error ? err.message : String(err) })
    }
  }, [])

  useEffect(() => {
    void fetchResults()
  }, [fetchResults])

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {load.kind === 'loading' && <Centered>Loading results…</Centered>}
      {load.kind === 'missing' && <NoResults onRetry={fetchResults} />}
      {load.kind === 'error' && (
        <Centered>
          <p className="text-[15px] text-ink">Couldn't load results.</p>
          <p className="mt-1 text-[13px] text-muted">{load.message}</p>
          <RetryButton onClick={fetchResults} />
        </Centered>
      )}
      {load.kind === 'ready' && <ResultsPage results={load.results} onReload={fetchResults} />}
    </div>
  )
}

function ResultsPage({ results, onReload }: { results: Results; onReload: () => void }) {
  const { risk_ranked: rr, list_order: lo } = results.runs
  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-5 px-6 py-5">
      <SourceStrip results={results} onReload={onReload} />
      <Headline results={results} />
      {/* Without a trap case (scripted calls) the chart takes the full width; the trap report says why. */}
      <div className={`grid gap-5 ${results.trap_case ? 'xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]' : ''}`}>
        <Card
          title="Urgent homes with help on the way"
          aside={<CurveLegend />}
          source={`runs.*.curve · ${fmtInt(rr.true_urgent)} truly urgent of ${fmtInt(rr.affected)} affected homes`}
        >
          <CurveChart rollcall={rr.curve} listOrder={lo.curve} trueUrgent={rr.true_urgent} />
        </Card>
        {results.trap_case && <TrapCaseCard trap={results.trap_case} />}
      </div>
      <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <TrapReport traps={results.traps} calls={results.source.calls} />
        <TriageCompareCard results={results} />
        <TargetsCard results={results} />
      </div>
    </div>
  )
}

// ---- Source and notes (the honesty strip) --------------------------------------------------

function SourceStrip({ results, onReload }: { results: Results; onReload: () => void }) {
  const s = results.source
  const simulated = s.calls.toLowerCase() === 'simulated'
  // A scripted run has no simulated residents, so naming their model would mislead.
  const residentModel = s.calls.toLowerCase() === 'scripted' ? null : s.resident_model
  const households =
    s.simulated_households && s.scripted_households
      ? `${fmtInt(s.simulated_households)} simulated + ${fmtInt(s.scripted_households)} scripted households`
      : `${fmtInt(s.simulated_households || s.scripted_households)} households`
  return (
    <section className="rounded-xl border border-line bg-panel px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span
          className={`rounded-md border px-2.5 py-1 text-[12.5px] font-bold uppercase tracking-[0.12em] ${
            simulated ? 'border-accent/45 bg-accent/15 text-accent' : 'border-line bg-raised text-ink'
          }`}
        >
          {simulated ? 'Simulated test' : `${s.calls} test`}
        </span>
        <span className="text-[14.5px] font-medium text-ink">{callsLabel(s)}</span>
        <Dot />
        <Fact label="Triage" value={triageLabel(s.triage)} />
        <Dot />
        <Fact label="Phone lines" value={fmtInt(s.lines)} />
        <Dot />
        <span className="text-[14px] text-muted">{households}</span>
        <div className="ml-auto flex items-center gap-3 text-[12.5px] text-faint">
          <span>
            {s.seed != null && <>seed {s.seed} · </>}
            {s.spec_hash && <>spec {s.spec_hash.slice(0, 8)} · </>}
            generated {formatTime(results.generated_at, TZ, false)} {zoneAbbrev(TZ, results.generated_at)}
            {usingFixture && <span className="ml-2 font-semibold uppercase tracking-wider text-accent">fixture data</span>}
          </span>
          <button
            type="button"
            onClick={onReload}
            className="rounded-md border border-line px-2.5 py-1 text-[12.5px] font-medium text-muted transition hover:bg-raised hover:text-ink"
          >
            Reload
          </button>
        </div>
      </div>
      {(s.voice_model || residentModel || s.tokens_per_household != null) && (
        <p className="mt-1.5 text-[12.5px] text-faint">
          {s.voice_model && <>Voice agent {s.voice_model}</>}
          {s.voice_model && residentModel && ' · '}
          {residentModel && <>simulated residents {residentModel}</>}
          {s.tokens_per_household != null && <> · {fmtInt(s.tokens_per_household)} tokens per household</>}
        </p>
      )}
      {results.notes.length > 0 && (
        <ul className="mt-3 grid gap-1 border-t border-line pt-3 text-[13.5px] text-muted md:grid-cols-2 2xl:grid-cols-3">
          {results.notes.map((n, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-faint" aria-hidden />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const Dot = () => <span className="text-faint" aria-hidden>·</span>

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-[14px] text-muted">
      {label} <span className="font-semibold text-ink">{value}</span>
    </span>
  )
}

// ---- Headline ------------------------------------------------------------------------------

function Headline({ results }: { results: Results }) {
  const { risk_ranked: rr, list_order: lo } = results.runs
  const median = formatSpeedup(speedupFor(results, 'median'))
  return (
    <section className="grid gap-5 rounded-xl border border-line bg-panel p-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      {/* Median: the 1:35 beat */}
      <div className="flex flex-col">
        <Eyebrow>Median minutes until an urgent home had help on the way</Eyebrow>
        <div className="mt-3 flex flex-wrap items-end gap-x-8 gap-y-3">
          <BigTime label="RollCall" value={rr.median_minutes_to_help} accent />
          <BigTime label="List order" value={lo.median_minutes_to_help} />
          <div className="pb-1.5">
            <SpeedupPill text={median.text} tone={median.tone} large />
          </div>
        </div>
        <p className="mt-4 text-[17px] leading-snug text-ink">
          Found <strong className="font-semibold">{fmtInt(rr.urgent_found)}</strong> of{' '}
          <strong className="font-semibold">{fmtInt(rr.true_urgent)}</strong> urgent homes. Every urgent home helped:{' '}
          <strong className="font-semibold">{formatAllUrgent(rr)}</strong> with RollCall,{' '}
          <strong className="font-semibold">{formatAllUrgent(lo)}</strong> in list order.
        </p>
        <Source>runs.risk_ranked vs runs.list_order · same conversations and triage, only the call order differs</Source>
      </div>

      <div className="grid content-start gap-4">
        <div className="grid grid-cols-3 gap-3">
          <Stat
            label="Urgent found"
            value={formatPct(rr.urgent_found_pct)}
            detail={`${fmtInt(rr.urgent_found)} of ${fmtInt(rr.true_urgent)}`}
            compare={lo.urgent_found_pct !== rr.urgent_found_pct ? `list order ${formatPct(lo.urgent_found_pct)}` : undefined}
          />
          <Stat
            label="False alarms"
            value={formatPct(rr.false_alarm_pct)}
            detail={`${fmtInt(rr.false_alarms)} of ${fmtInt(rr.flagged_urgent)} flagged`}
            compare={lo.false_alarm_pct !== rr.false_alarm_pct ? `list order ${formatPct(lo.false_alarm_pct)}` : undefined}
          />
          <Stat
            label="Coverage"
            value={formatPct(rr.coverage_pct)}
            detail={`${fmtInt(rr.affected)} homes · ${fmtInt(rr.calls)} calls`}
            compare={lo.coverage_pct !== rr.coverage_pct ? `list order ${formatPct(lo.coverage_pct)}` : undefined}
          />
        </div>
        <SlowerTable results={results} />
      </div>
    </section>
  )
}

function BigTime({ label, value, accent = false }: { label: string; value: number | null; accent?: boolean }) {
  const text = formatMinutes(value)
  const [num, unit] = text === '—' ? ['—', ''] : [text.replace(/ min$/, ''), 'min']
  return (
    <div>
      <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.12em] text-muted">
        <span className={`h-[3px] w-4 rounded-full ${accent ? 'bg-accent' : 'bg-faint'}`} aria-hidden />
        {label}
      </div>
      <div className={`mt-1 tabular-nums leading-none ${accent ? 'text-ink' : 'text-muted'}`}>
        <span className="text-[64px] font-semibold tracking-tight short:text-[52px]">{num}</span>
        {unit && <span className="ml-1.5 text-[22px] font-medium text-muted">{unit}</span>}
      </div>
    </div>
  )
}

function SpeedupPill({ text, tone, large = false }: { text: string; tone: string; large?: boolean }) {
  const cls =
    tone === 'faster'
      ? 'border-accent/45 bg-accent/15 text-accent'
      : tone === 'slower'
        ? 'border-bad/45 bg-bad/10 text-bad'
        : 'border-line bg-raised text-muted'
  return (
    <span className={`inline-block rounded-lg border font-semibold tabular-nums ${cls} ${large ? 'px-3.5 py-1.5 text-[22px]' : 'px-2 py-0.5 text-[13px]'}`}>
      {text}
    </span>
  )
}

function Stat({ label, value, detail, compare }: { label: string; value: string; detail: string; compare?: string }) {
  return (
    <div className="rounded-lg border border-line bg-bg/40 px-3.5 py-3">
      <div className="text-[11.5px] font-semibold uppercase tracking-[0.12em] text-muted">{label}</div>
      <div className="mt-1 text-[30px] font-semibold leading-none tabular-nums text-ink">{value}</div>
      <div className="mt-1.5 text-[12.5px] tabular-nums text-muted">{detail}</div>
      {compare && <div className="text-[12px] tabular-nums text-faint">{compare}</div>}
    </div>
  )
}

const SLOWER: { key: SpeedupKey; label: string; pick: (r: ResultsRun) => string }[] = [
  { key: 'median', label: 'Median', pick: (r) => formatMinutes(r.median_minutes_to_help) },
  { key: 'p90', label: '90% of urgent homes', pick: (r) => formatMinutes(r.p90_minutes_to_help) },
  { key: 'all', label: 'Every urgent home', pick: formatAllUrgent },
]

function SlowerTable({ results }: { results: Results }) {
  const { risk_ranked: rr, list_order: lo } = results.runs
  return (
    <table className="w-full text-[14px]">
      <thead>
        <tr className="text-left text-[11.5px] font-semibold uppercase tracking-[0.12em] text-faint">
          <th className="pb-1.5 font-semibold">Minutes until help</th>
          <th className="pb-1.5 text-right font-semibold">RollCall</th>
          <th className="pb-1.5 text-right font-semibold">List order</th>
          <th className="pb-1.5 text-right font-semibold">Ratio</th>
        </tr>
      </thead>
      <tbody>
        {SLOWER.map((row) => {
          const s = formatSpeedup(speedupFor(results, row.key))
          return (
            <tr key={row.key} className="border-t border-line">
              <td className="py-1.5 text-muted">{row.label}</td>
              <td className="py-1.5 text-right font-semibold tabular-nums text-ink">{row.pick(rr)}</td>
              <td className="py-1.5 text-right tabular-nums text-muted">{row.pick(lo)}</td>
              <td className="py-1.5 text-right">
                <SpeedupPill text={s.text} tone={s.tone} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---- Trap case (the 1:20 beat) -------------------------------------------------------------

function TrapCaseCard({ trap }: { trap: TrapCase }) {
  const kw = splitVerdict(trap.keyword_said)
  const nm = splitVerdict(trap.nemotron_said)
  return (
    <Card
      title="Trap case: same words, two verdicts"
      aside={`${personaLabel(trap.persona)} · household ${trap.household_id}`}
      source="trap_case · keyword rules and Nemotron judged the same transcript"
    >
      <figure className="px-1">
        <blockquote className="text-[24px] font-medium leading-snug text-ink short:text-[21px]">“{trap.quote}”</blockquote>
        <figcaption className="mt-2 text-[13.5px] text-muted">{trap.name}, simulated resident</figcaption>
      </figure>
      <div className="mt-5 grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-line bg-bg/40 p-4">
          <div className="text-[11.5px] font-semibold uppercase tracking-[0.12em] text-muted">Keywords said</div>
          <div className="mt-2 flex items-center gap-2 text-[26px] font-semibold leading-tight text-muted">
            <CrossIcon />
            <span className="line-through decoration-bad/70 decoration-2">{kw.tag}</span>
          </div>
          {kw.reason && <div className="mt-1.5 text-[13.5px] text-muted">{kw.reason}</div>}
        </div>
        <div className="rounded-lg border border-accent/50 bg-accent/10 p-4">
          <div className="text-[11.5px] font-semibold uppercase tracking-[0.12em] text-accent">Nemotron said</div>
          <div className="mt-2 flex items-center gap-2 text-[26px] font-semibold leading-tight text-ink">
            <CheckIcon />
            {nm.tag}
          </div>
          {nm.reason && <div className="mt-1.5 text-[13.5px] text-ink">{nm.reason}</div>}
        </div>
      </div>
      {trap.transcript.length > 0 && (
        <details className="group mt-4 rounded-lg border border-line">
          <summary className="cursor-pointer select-none px-4 py-2.5 text-[13.5px] font-medium text-muted hover:text-ink">
            Full transcript ({trap.transcript.length} lines)
          </summary>
          <ol className="max-h-[300px] space-y-2 overflow-y-auto border-t border-line px-4 py-3 text-[14px]">
            {trap.transcript.map((t, i) => (
              <li key={i} className="flex gap-3">
                <span className={`w-[70px] shrink-0 text-[12px] font-semibold uppercase tracking-wider ${t.role === 'agent' ? 'text-faint' : 'text-accent'}`}>
                  {t.role === 'agent' ? 'Agent' : 'Resident'}
                </span>
                <span className={t.role === 'agent' ? 'text-muted' : 'text-ink'}>{t.line}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </Card>
  )
}

// ---- Trap report, triage compare, latency, targets ------------------------------------------

function TrapReport({ traps, calls }: { traps: TrapResult[]; calls: string }) {
  const passed = traps.filter((t) => t.passed).length
  return (
    <Card
      title="Trap report by persona"
      aside={traps.length ? `${passed} of ${traps.length} passed` : undefined}
      source="traps · each persona scored against its written rule"
    >
      {traps.length === 0 ? (
        <Empty>
          No trap case or trap report: this run used <strong className="text-ink">{calls}</strong> calls, so there are no free-speech
          transcripts to judge. Run the evaluation with simulated calls to get the side-by-side.
        </Empty>
      ) : (
        <div className="max-h-[420px] overflow-y-auto">
          <table className="w-full text-[13.5px]">
            <thead className="sticky top-0 bg-panel">
              <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">
                <th className="pb-1.5 font-semibold">Persona</th>
                <th className="pb-1.5 font-semibold">Result</th>
                <th className="pb-1.5 text-center font-semibold">True</th>
                <th className="pb-1.5 text-center font-semibold">Found</th>
                <th className="pb-1.5 text-center font-semibold">Keywords</th>
              </tr>
            </thead>
            <tbody>
              {traps.map((t) => (
                <tr key={`${t.household_id}-${t.persona}`} className="border-t border-line align-top" title={t.summary ?? undefined}>
                  <td className="py-2 pr-2">
                    <div className="font-medium text-ink">{personaLabel(t.persona)}</div>
                    <div className="text-[12px] text-faint">{t.rule}</div>
                  </td>
                  <td className="py-2 pr-2">
                    {t.passed ? (
                      <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                        <CheckIcon small /> Pass
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 font-semibold text-bad">
                        <CrossIcon small /> Fail
                      </span>
                    )}
                  </td>
                  <td className="py-2 text-center">
                    <Urgency u={t.true_urgency} />
                  </td>
                  <td className="py-2 text-center">
                    <Urgency u={t.found_urgency} compareTo={t.true_urgency} />
                  </td>
                  <td className="py-2 text-center">
                    <Urgency u={t.keyword_urgency} compareTo={t.true_urgency} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** An urgency tag; underlined in red when it is less urgent than the truth (the dangerous miss). */
function Urgency({ u, compareTo }: { u: string | null; compareTo?: string | null }) {
  if (!u) return <span className="text-faint">—</span>
  const a = urgencyRank(u)
  const b = urgencyRank(compareTo)
  const under = compareTo !== undefined && a != null && b != null && a > b
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[12.5px] font-semibold tabular-nums ${under ? 'bg-bad/12 text-bad' : 'bg-raised text-ink'}`}
      title={under ? 'Less urgent than the truth' : undefined}
    >
      {u}
    </span>
  )
}

function TriageCompareCard({ results }: { results: Results }) {
  const tc = results.triage_compare
  const lat = results.latency
  return (
    <Card title="Keywords vs Nemotron" source={tc ? `triage_compare · the same ${fmtInt(tc.households)} transcripts, judged twice` : 'triage_compare is null'}>
      {!tc ? (
        <Empty>No comparison: with {results.source.calls} calls there are no transcripts for the keyword rules to read.</Empty>
      ) : (
        <table className="w-full text-[14px]">
          <thead>
            <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">
              <th className="pb-1.5 font-semibold" />
              <th className="pb-1.5 text-right font-semibold">Urgent found</th>
              <th className="pb-1.5 text-right font-semibold">False alarms</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-line">
              <td className="py-2.5 font-semibold text-ink">
                <span className="mr-2 inline-block h-[3px] w-3.5 rounded-full bg-accent align-middle" aria-hidden />
                Nemotron
              </td>
              <td className="py-2.5 text-right text-[22px] font-semibold tabular-nums text-ink">{formatPct(tc.nemotron.urgent_found_pct)}</td>
              <td className="py-2.5 text-right text-[22px] font-semibold tabular-nums text-ink">{formatPct(tc.nemotron.false_alarm_pct)}</td>
            </tr>
            <tr className="border-t border-line">
              <td className="py-2.5 text-muted">
                <span className="mr-2 inline-block h-[3px] w-3.5 rounded-full bg-faint align-middle" aria-hidden />
                Keyword rules
              </td>
              <td className="py-2.5 text-right text-[22px] tabular-nums text-muted">{formatPct(tc.keywords.urgent_found_pct)}</td>
              <td className="py-2.5 text-right text-[22px] tabular-nums text-muted">{formatPct(tc.keywords.false_alarm_pct)}</td>
            </tr>
          </tbody>
        </table>
      )}

      <div className="mt-5 border-t border-line pt-4">
        <Eyebrow>Live reply delay (real calls)</Eyebrow>
        {lat ? (
          <>
            <div className="mt-2 flex gap-8">
              <Latency label="Median" ms={lat.p50_ms} />
              <Latency label="90th percentile" ms={lat.p90_ms} />
            </div>
            <Source>latency · {fmtInt(lat.turns)} agent turns logged on real phone calls</Source>
          </>
        ) : (
          <p className="mt-2 text-[13.5px] text-muted">Not measured yet: no real calls logged (latency is null).</p>
        )}
      </div>
    </Card>
  )
}

function Latency({ label, ms }: { label: string; ms: number }) {
  return (
    <div>
      <div className="text-[12.5px] text-muted">{label}</div>
      <div className="text-[26px] font-semibold tabular-nums text-ink">
        {ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`}
      </div>
    </div>
  )
}

function TargetsCard({ results }: { results: Results }) {
  const t = results.targets
  const met = t.filter((x) => x.met).length
  return (
    <Card title="PRD targets" aside={t.length ? `${met} of ${t.length} met` : undefined} source="targets · checked by scripts.run_eval">
      {t.length === 0 ? (
        <Empty>No targets in these results.</Empty>
      ) : (
        <ul className="divide-y divide-line">
          {t.map((x) => (
            <li key={x.name} className="flex items-start gap-3 py-2.5">
              <span className="mt-0.5">{x.met ? <CheckIcon small /> : <CrossIcon small />}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[14px] font-medium text-ink">{x.name}</div>
                <div className="text-[12.5px] text-faint">target {x.target}</div>
              </div>
              <div className="text-right">
                <div className="text-[15px] font-semibold tabular-nums text-ink">{x.value}</div>
                <div className={`text-[12px] font-semibold ${x.met ? 'text-muted' : 'text-bad'}`}>{x.met ? 'Met' : 'Not met'}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// ---- Building blocks ------------------------------------------------------------------------

function Card({ title, aside, source, children }: { title: string; aside?: ReactNode; source?: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col rounded-xl border border-line bg-panel px-5 pt-4 pb-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.14em] text-muted">{title}</h2>
        {aside != null && <div className="text-[12.5px] text-faint">{aside}</div>}
      </header>
      <div className="flex-1">{children}</div>
      {source && <Source>{source}</Source>}
    </section>
  )
}

function Source({ children }: { children: ReactNode }) {
  return <p className="mt-3 text-[11.5px] text-faint">Source: {children}</p>
}

function Eyebrow({ children }: { children: ReactNode }) {
  return <div className="text-[12px] font-semibold uppercase tracking-[0.14em] text-muted">{children}</div>
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-line px-4 py-6 text-[14px] leading-relaxed text-muted">{children}</p>
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full flex-col items-center justify-center p-10 text-center text-[15px] text-muted">{children}</div>
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-4 rounded-lg border border-line bg-panel px-3.5 py-2 text-[14px] font-medium text-ink transition hover:bg-raised"
    >
      Try again
    </button>
  )
}

function NoResults({ onRetry }: { onRetry: () => void }) {
  return (
    <Centered>
      <p className="text-[18px] font-medium text-ink">No results yet</p>
      <p className="mt-2 max-w-[520px] text-[14px] text-muted">
        GET /results answers 404 until the evaluation has run. It runs the city twice (risk-ranked and list order) and writes
        results/latest.json:
      </p>
      <pre className="mt-4 rounded-lg border border-line bg-panel px-4 py-3 text-left text-[14px] text-ink">
        <code>cd backend; uv run python -m scripts.run_eval</code>
      </pre>
      <RetryButton onClick={onRetry} />
    </Centered>
  )
}

function CheckIcon({ small = false }: { small?: boolean }) {
  const s = small ? 15 : 22
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}

function CrossIcon({ small = false }: { small?: boolean }) {
  const s = small ? 15 : 22
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="var(--bad)" strokeWidth="2.6" strokeLinecap="round" aria-hidden className="shrink-0">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}
