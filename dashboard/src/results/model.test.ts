import { describe, expect, it } from 'vitest'
import type { Results, ResultsRun } from '../api/types'
import { fixtureResults } from '../fixture/fixtureResults'
import {
  buildCurveChart,
  cleanCurve,
  formatAllUrgent,
  formatMinutes,
  formatPct,
  formatSpeedup,
  nearestIndex,
  niceCeil,
  niceTicks,
  ratioOf,
  speedupFor,
  splitVerdict,
  urgencyRank,
  valueAt,
} from './model'

describe('formatSpeedup', () => {
  it('says how many times faster, one decimal', () => {
    expect(formatSpeedup(4.4).text).toBe('4.4× faster')
    expect(formatSpeedup(2).text).toBe('2.0× faster')
    expect(formatSpeedup(4.4).tone).toBe('faster')
  })
  it('calls a ratio near 1 "about the same" instead of a win', () => {
    expect(formatSpeedup(1.0)).toMatchObject({ text: 'about the same', tone: 'same' })
    expect(formatSpeedup(1.03).tone).toBe('same')
    expect(formatSpeedup(0.97).tone).toBe('same')
  })
  it('reports slower runs as slower', () => {
    expect(formatSpeedup(0.5)).toMatchObject({ text: '2.0× slower', tone: 'slower' })
  })
  it('handles missing values', () => {
    expect(formatSpeedup(null).tone).toBe('unknown')
    expect(formatSpeedup(0).tone).toBe('unknown')
    expect(formatSpeedup(Number.NaN).tone).toBe('unknown')
  })
})

describe('number formatting', () => {
  it('formats minutes', () => {
    expect(formatMinutes(3.2)).toBe('3.2 min')
    expect(formatMinutes(3)).toBe('3 min')
    expect(formatMinutes(52.3)).toBe('52 min')
    expect(formatMinutes(null)).toBe('—')
  })
  it('formats percents', () => {
    expect(formatPct(96.6)).toBe('96.6%')
    expect(formatPct(100)).toBe('100%')
    expect(formatPct(null)).toBe('—')
  })
  it('explains a missing "every urgent home" time', () => {
    const run = { minutes_until_all_urgent_helped: null, true_urgent: 116, urgent_found: 112 } as ResultsRun
    expect(formatAllUrgent(run)).toBe('never (4 missed)')
    expect(formatAllUrgent({ ...run, minutes_until_all_urgent_helped: 52.3 })).toBe('52 min')
  })
})

describe('speedup', () => {
  it('uses the backend value when present, else the ratio of the runs', () => {
    const r = fixtureResults(null) as Results
    expect(speedupFor(r, 'median')).toBe(4.4)
    r.speedup = null
    expect(speedupFor(r, 'median')).toBeCloseTo(14.1 / 3.2)
    expect(speedupFor(r, 'all')).toBeNull() // neither run helped every urgent home
  })
  it('ratioOf refuses zero or missing inputs', () => {
    expect(ratioOf(0, 5)).toBeNull()
    expect(ratioOf(null, 5)).toBeNull()
    expect(ratioOf(2, 5)).toBe(2.5)
  })
})

describe('verdicts', () => {
  it('splits tag and reason', () => {
    expect(splitVerdict('P2 urgent: oxygen on battery')).toEqual({ tag: 'P2 urgent', reason: 'oxygen on battery' })
    expect(splitVerdict('P4 safe')).toEqual({ tag: 'P4 safe', reason: null })
  })
  it('ranks urgency', () => {
    expect(urgencyRank('P2')).toBe(2)
    expect(urgencyRank('p1 emergency')).toBe(1)
    expect(urgencyRank(null)).toBeNull()
    expect(urgencyRank('safe')).toBeNull()
  })
})

describe('curve scaling', () => {
  it('picks nice ticks that reach the max', () => {
    expect(niceCeil(7)).toBe(10)
    expect(niceCeil(23.2)).toBe(25)
    expect(niceTicks(116, 5)).toEqual([0, 25, 50, 75, 100, 125])
    expect(niceTicks(56, 6)).toEqual([0, 10, 20, 30, 40, 50, 60])
    expect(niceTicks(0)).toEqual([0, 1])
  })

  it('maps minutes and counts into the plot box, y axis reaching the true urgent count', () => {
    const box = { width: 600, height: 300, left: 50, right: 50, top: 20, bottom: 40 }
    const rr = cleanCurve([[10, 70], [0, 0], [5, 41]])
    const lo = cleanCurve([[0, 0], [5, 10], [10, 30]])
    const chart = buildCurveChart([rr, lo], box, 116)
    expect(chart.xMax).toBe(10)
    expect(chart.yMax).toBe(125)
    expect(chart.x(0)).toBe(50)
    expect(chart.x(10)).toBe(550)
    expect(chart.y(0)).toBe(260)
    expect(chart.y(125)).toBe(20)
    expect(chart.paths[0].startsWith('M50,260L300,')).toBe(true)
    expect(chart.minutes).toEqual([0, 5, 10])
  })

  it('drops malformed points and sorts by minute', () => {
    expect(cleanCurve([[5, 2], [Number.NaN, 1], [1, 1]] as [number, number][])).toEqual([
      [1, 1],
      [5, 2],
    ])
    expect(cleanCurve(null)).toEqual([])
  })

  it('reads the value at a minute and the nearest sample', () => {
    const c: [number, number][] = [[0, 0], [5, 41], [10, 70]]
    expect(valueAt(c, 7)).toBe(41)
    expect(valueAt(c, 10)).toBe(70)
    expect(nearestIndex([0, 5, 10], 7.6)).toBe(2)
    expect(nearestIndex([], 3)).toBe(-1)
  })
})

describe('fixture results', () => {
  it('has a trap case by default and none for scripted calls', () => {
    expect(fixtureResults(null)?.trap_case?.keyword_said).toBe('P4 safe')
    const scripted = fixtureResults('scripted')
    expect(scripted?.trap_case).toBeNull()
    expect(scripted?.traps).toEqual([])
    expect(fixtureResults('noresults')).toBeNull()
  })
  it('curves share the same minutes', () => {
    const r = fixtureResults(null) as Results
    expect(r.runs.risk_ranked.curve.map((p) => p[0])).toEqual(r.runs.list_order.curve.map((p) => p[0]))
  })
})
