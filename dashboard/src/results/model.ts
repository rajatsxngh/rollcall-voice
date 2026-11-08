import type { Results, ResultsRun, ResultsSource } from '../api/types'

/** Pure helpers for the results view (tested in model.test.ts). */

const oneDecimal = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const upToOne = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const integer = new Intl.NumberFormat('en-US')

const finite = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n)

/** "3.2 min", "52 min"; "—" when unknown. Whole minutes from 10 up, so big numbers read fast. */
export function formatMinutes(m: number | null | undefined): string {
  if (!finite(m)) return '—'
  return `${m >= 10 ? integer.format(Math.round(m)) : upToOne.format(m)} min`
}

/** "96.6%"; "—" when unknown. */
export function formatPct(p: number | null | undefined): string {
  return finite(p) ? `${upToOne.format(p)}%` : '—'
}

export type SpeedupTone = 'faster' | 'same' | 'slower' | 'unknown'

/**
 * A ratio (list order time / RollCall time) in words. Within 5% of 1 is "about the same":
 * the honest reading of 1.0×, so a small speedup is never dressed up as a win.
 */
export function formatSpeedup(ratio: number | null | undefined): { text: string; short: string; tone: SpeedupTone } {
  if (!finite(ratio) || ratio <= 0) return { text: 'no comparison', short: '—', tone: 'unknown' }
  if (ratio >= 1.05) {
    const x = `${oneDecimal.format(ratio)}×`
    return { text: `${x} faster`, short: x, tone: 'faster' }
  }
  if (ratio > 1 / 1.05) return { text: 'about the same', short: '≈1×', tone: 'same' }
  const x = `${oneDecimal.format(1 / ratio)}×`
  return { text: `${x} slower`, short: x, tone: 'slower' }
}

/** Ratio of list order to RollCall for one time metric; null when either side is missing or zero. */
export function ratioOf(rollcall: number | null | undefined, listOrder: number | null | undefined): number | null {
  if (!finite(rollcall) || !finite(listOrder) || rollcall <= 0) return null
  return listOrder / rollcall
}

export type SpeedupKey = 'median' | 'p90' | 'all'

/** The backend's speedup if it gave one, else computed from the two runs. */
export function speedupFor(results: Results, key: SpeedupKey): number | null {
  const given = results.speedup?.[key]
  if (finite(given)) return given
  const { risk_ranked: rr, list_order: lo } = results.runs
  const field = metricField(key)
  return ratioOf(rr[field], lo[field])
}

export function metricField(key: SpeedupKey): 'median_minutes_to_help' | 'p90_minutes_to_help' | 'minutes_until_all_urgent_helped' {
  return key === 'median' ? 'median_minutes_to_help' : key === 'p90' ? 'p90_minutes_to_help' : 'minutes_until_all_urgent_helped'
}

/** "Every urgent home" time, or why there isn't one. */
export function formatAllUrgent(run: ResultsRun): string {
  if (finite(run.minutes_until_all_urgent_helped)) return formatMinutes(run.minutes_until_all_urgent_helped)
  const missed = Math.max(0, run.true_urgent - (run.true_urgent_helped ?? run.urgent_found))
  return missed > 0 ? `never (${integer.format(missed)} missed)` : '—'
}

/** One line for the 1:35 beat: "Found 112 of 116 urgent homes in 52 min. Calling in list order took 52 min." */
export function headline(results: Results): string {
  const { risk_ranked: rr, list_order: lo } = results.runs
  return (
    `Found ${integer.format(rr.urgent_found)} of ${integer.format(rr.true_urgent)} urgent homes; ` +
    `half had help within ${formatMinutes(rr.median_minutes_to_help)}. ` +
    `Calling in list order: ${formatMinutes(lo.median_minutes_to_help)}.`
  )
}

/** "Simulated calls (Nemotron plays residents)" etc. */
export function callsLabel(source: ResultsSource): string {
  const c = source.calls.toLowerCase()
  if (c === 'simulated') return 'Simulated calls: Nemotron plays the residents'
  if (c === 'scripted') return 'Scripted calls: answers read from the hidden truth, no AI'
  if (c === 'mixed') return 'Mixed: some simulated, some scripted calls'
  return `${source.calls} calls`
}

export function triageLabel(triage: string): string {
  const t = triage.toLowerCase()
  if (t === 'nemotron') return 'Nemotron'
  if (t === 'keywords' || t === 'keyword') return 'Keyword rules'
  if (t === 'rules' || t === 'scripted') return 'Rules (no AI)'
  return triage
}

/** "P2" -> 2; anything else -> null. */
export function urgencyRank(u: string | null | undefined): number | null {
  const m = /^P([1-4])\b/i.exec(u ?? '')
  return m ? Number(m[1]) : null
}

/** Splits "P2 urgent: oxygen on battery" into the tag ("P2 urgent") and the reason. */
export function splitVerdict(said: string): { tag: string; reason: string | null } {
  const i = said.indexOf(':')
  if (i < 0) return { tag: said.trim(), reason: null }
  return { tag: said.slice(0, i).trim(), reason: said.slice(i + 1).trim() || null }
}

/** "downplays" -> "Downplays", "call_my_daughter" -> "Call my daughter". */
export function personaLabel(p: string): string {
  const s = p.replace(/[_-]+/g, ' ').trim()
  return s ? s[0].toUpperCase() + s.slice(1) : p
}

// ---- Chart geometry ------------------------------------------------------------------------

/** Smallest "nice" number (1, 2, 2.5, 5 × 10^k) at or above n. */
export function niceCeil(n: number): number {
  if (!(n > 0)) return 1
  const exp = Math.floor(Math.log10(n))
  const base = 10 ** exp
  for (const step of [1, 2, 2.5, 5, 10]) {
    const v = step * base
    if (v >= n - 1e-9) return v
  }
  return 10 * base
}

/**
 * About `count` evenly spaced nice ticks from 0 to at least max (the last tick is the axis end).
 * Steps are whole numbers: both axes count minutes or homes.
 */
export function niceTicks(max: number, count = 5): number[] {
  const step = Math.max(1, niceCeil((max > 0 ? max : 1) / count))
  const end = Math.ceil((max > 0 ? max : 1) / step - 1e-9) * step
  const out: number[] = []
  for (let v = 0; v <= end + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6)
  return out
}

export interface ChartBox {
  width: number
  height: number
  left: number
  right: number
  top: number
  bottom: number
}

export interface CurveChart {
  xTicks: number[]
  yTicks: number[]
  xMax: number
  yMax: number
  x(minute: number): number
  y(count: number): number
  /** SVG path "M..L.." per series, in input order. */
  paths: string[]
  /** Sorted unique minutes across series (for the hover crosshair). */
  minutes: number[]
}

/** Sorts a curve by minute and drops malformed points. */
export function cleanCurve(curve: readonly (readonly [number, number])[] | null | undefined): [number, number][] {
  return (curve ?? [])
    .filter((p) => Array.isArray(p) && finite(p[0]) && finite(p[1]))
    .map((p) => [p[0], p[1]] as [number, number])
    .sort((a, b) => a[0] - b[0])
}

/**
 * Scales curves into a box. The y axis always reaches `yAtLeast` (the true urgent count), so a
 * run that missed homes visibly stops short of the top line.
 */
export function buildCurveChart(series: [number, number][][], box: ChartBox, yAtLeast = 0): CurveChart {
  let maxX = 0
  let maxY = yAtLeast
  for (const s of series)
    for (const [m, c] of s) {
      if (m > maxX) maxX = m
      if (c > maxY) maxY = c
    }
  const xTicks = niceTicks(maxX, 6)
  const yTicks = niceTicks(maxY, 5)
  const xMax = xTicks[xTicks.length - 1]
  const yMax = yTicks[yTicks.length - 1]
  const plotW = box.width - box.left - box.right
  const plotH = box.height - box.top - box.bottom
  const x = (m: number) => box.left + (m / xMax) * plotW
  const y = (c: number) => box.top + plotH - (c / yMax) * plotH
  const r = (v: number) => Math.round(v * 10) / 10
  const paths = series.map((s) => s.map(([m, c], i) => `${i ? 'L' : 'M'}${r(x(m))},${r(y(c))}`).join(''))
  const minutes = [...new Set(series.flatMap((s) => s.map((p) => p[0])))].sort((a, b) => a - b)
  return { xTicks, yTicks, xMax, yMax, x, y, paths, minutes }
}

/** The curve's value at a minute: the last point at or before it (counts only go up). */
export function valueAt(curve: [number, number][], minute: number): number | null {
  let v: number | null = null
  for (const [m, c] of curve) {
    if (m > minute) break
    v = c
  }
  return v
}

/** Index into a sorted list of the value nearest to `v`. */
export function nearestIndex(sorted: number[], v: number): number {
  if (!sorted.length) return -1
  let best = 0
  for (let i = 1; i < sorted.length; i++) if (Math.abs(sorted[i] - v) < Math.abs(sorted[best] - v)) best = i
  return best
}
