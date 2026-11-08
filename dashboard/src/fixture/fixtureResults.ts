import type { Results, ResultsRun } from '../api/types'

/**
 * A made-up but internally consistent GET /results for ?fixture (UI work only; the header says
 * FIXTURE DATA). `?fixture=scripted` gives the scripted-calls shape (no traps, no trap case);
 * `?fixture=noresults` makes /results answer 404.
 */

/** Piecewise-linear curve through (minute, count) knots, sampled every minute up to `end`. */
function sample(knots: [number, number][], end: number): [number, number][] {
  const out: [number, number][] = []
  for (let m = 0; m <= end; m++) {
    let v = knots[knots.length - 1][1]
    for (let i = 1; i < knots.length; i++) {
      const [x0, y0] = knots[i - 1]
      const [x1, y1] = knots[i]
      if (m <= x1) {
        v = y0 + ((m - x0) / (x1 - x0)) * (y1 - y0)
        break
      }
    }
    out.push([m, Math.round(v)])
  }
  return out
}

const END = 56

const base = {
  affected: 1365,
  calls: 1709,
  coverage_pct: 100.0,
  true_urgent: 116,
  urgent_found: 112,
  urgent_found_pct: 96.6,
  flagged_urgent: 120,
  false_alarms: 8,
  false_alarm_pct: 6.7,
  avg_call_minutes: 1.8,
}

const riskRanked: ResultsRun = {
  ...base,
  median_minutes_to_help: 3.2,
  p90_minutes_to_help: 21.0,
  minutes_until_all_urgent_helped: null,
  minutes_total: 54.1,
  curve: sample([[0, 0], [1, 12], [3.2, 56], [8, 82], [21, 101], [35, 108], [52.3, 112]], END),
}

const listOrder: ResultsRun = {
  ...base,
  calls: 1712,
  median_minutes_to_help: 14.1,
  p90_minutes_to_help: 44.1,
  minutes_until_all_urgent_helped: null,
  minutes_total: 54.6,
  curve: sample([[0, 0], [2, 4], [14.1, 56], [30, 84], [44.1, 101], [52.8, 112]], END),
}

const TRAPS: Results['traps'] = [
  { household_id: 812, persona: 'downplays', true_urgency: 'P2', found_urgency: 'P2', keyword_urgency: 'P4', passed: true, rule: 'flagged P1 or P2', summary: 'Says she is fine; oxygen concentrator on battery backup.' },
  { household_id: 233, persona: 'hangs_up', true_urgency: 'P3', found_urgency: null, keyword_urgency: null, passed: true, rule: 'follow-up scheduled, not marked safe', summary: 'Hung up after the greeting; call-back booked in 10 minutes.' },
  { household_id: 1450, persona: 'call_my_daughter', true_urgency: 'P3', found_urgency: 'P3', keyword_urgency: 'P4', passed: true, rule: 'second contact saved and followed up', summary: "Gave her daughter's number; text sent to the daughter." },
  { household_id: 977, persona: 'spanish_only', true_urgency: 'P2', found_urgency: 'P2', keyword_urgency: 'P4', passed: true, rule: 'not marked safe, routed to Spanish help', summary: 'Spoke only Spanish; handed to a Spanish-speaking dispatcher.' },
  { household_id: 1288, persona: 'hard_of_hearing', true_urgency: 'P3', found_urgency: 'P3', keyword_urgency: 'P3', passed: true, rule: 'finished or escalated, never guessed safe', summary: 'Asked for repeats; agent slowed down and finished the check.' },
  { household_id: 64, persona: 'confused', true_urgency: 'P2', found_urgency: 'P4', keyword_urgency: 'P4', passed: false, rule: 'finished or escalated, never guessed safe', summary: 'Mixed up the day; agent marked safe instead of escalating.' },
  { household_id: 1702, persona: 'angry', true_urgency: 'P4', found_urgency: 'P4', keyword_urgency: 'P2', passed: true, rule: 'finished or escalated, never guessed safe', summary: 'Shouted about the robocall, then confirmed all fine.' },
  { household_id: 405, persona: 'trapped', true_urgency: 'P1', found_urgency: 'P1', keyword_urgency: 'P1', passed: true, rule: 'flagged P1, handed to a human', summary: 'Tree on the roof, can not get out; P1 handoff to 911.' },
]

const SIMULATED: Results = {
  generated_at: '2026-11-08T03:10:00.000Z',
  source: { calls: 'simulated', simulated_households: 1365, scripted_households: 0, triage: 'nemotron', lines: 100, seed: 42, spec_hash: 'a1b2c3d4e5f6' },
  runs: { risk_ranked: riskRanked, list_order: listOrder },
  speedup: { median: 4.4, p90: 2.1, all: null },
  triage_compare: {
    households: 1270,
    nemotron: { urgent_found_pct: 96.6, false_alarm_pct: 6.7 },
    keywords: { urgent_found_pct: 61.2, false_alarm_pct: 18.0 },
  },
  traps: TRAPS,
  trap_case: {
    household_id: 812,
    name: 'Dorothy Smith',
    persona: 'downplays',
    quote: "I'm fine, honey, the oxygen machine just keeps beeping",
    keyword_said: 'P4 safe',
    nemotron_said: 'P2 urgent: oxygen on battery',
    transcript: [
      { role: 'agent', line: "Hi, this is RollCall, an automated check-in from Richardson emergency services. There's a tornado warning in your area. Is this Dorothy?" },
      { role: 'resident', line: 'Yes, this is Dorothy.' },
      { role: 'agent', line: 'Are you safe right now, and is anyone with you?' },
      { role: 'resident', line: "Oh, I'm fine, honey. I'm by myself, but I'm fine." },
      { role: 'agent', line: 'Do you have power at the moment?' },
      { role: 'resident', line: "The lights went out a bit ago. I'm fine, honey, the oxygen machine just keeps beeping." },
      { role: 'agent', line: 'Is that your oxygen concentrator? Is it running on its battery?' },
      { role: 'resident', line: 'I think so. It does that when the power is out. It usually lasts a couple of hours.' },
      { role: 'agent', line: "Thank you, Dorothy. I'm sending a crew to check on you and bring power for the machine. Stay where you are; they'll be there soon." },
    ],
  },
  latency: { turns: 42, p50_ms: 640, p90_ms: 1110 },
  targets: [
    { name: 'Urgent households found', target: '>= 95%', value: '96.6%', met: true },
    { name: 'False alarms', target: '<= 10%', value: '6.7%', met: true },
    { name: 'Coverage', target: '100%', value: '100%', met: true },
    { name: 'Median time to help vs list order', target: 'reported', value: '4.4× faster', met: true },
    { name: 'Trap personas handled', target: '30 of 30', value: '7 of 8 shown', met: false },
  ],
  notes: [
    'The 2,000-household test is simulated: Nemotron plays the residents from each household’s hidden truth.',
    '100 phone lines; both runs use the same conversations and cached triage, so only the call order differs.',
    'Minutes are run-clock minutes from the alert. “Help on the way” means a crew, bed or adjuster was assigned, or a P1 was handed to a human.',
  ],
}

const SCRIPTED: Results = {
  ...SIMULATED,
  source: { calls: 'scripted', simulated_households: 0, scripted_households: 1365, triage: 'rules', lines: 100, seed: 42, spec_hash: null },
  runs: {
    risk_ranked: { ...riskRanked, urgent_found: 116, urgent_found_pct: 100, flagged_urgent: 116, false_alarms: 0, false_alarm_pct: 0, minutes_until_all_urgent_helped: 51.0 },
    list_order: { ...listOrder, urgent_found: 116, urgent_found_pct: 100, flagged_urgent: 116, false_alarms: 0, false_alarm_pct: 0, minutes_until_all_urgent_helped: 52.4 },
  },
  speedup: { median: 4.4, p90: 2.1, all: 1.03 },
  triage_compare: null,
  traps: [],
  trap_case: null,
  latency: null,
  targets: [{ name: 'Coverage', target: '100%', value: '100%', met: true }],
  notes: ['Scripted calls: answers come straight from the hidden truth, so triage is perfect by construction.', '100 phone lines.'],
}

export function fixtureResults(variant: string | null): Results | null {
  if (variant === 'noresults') return null
  return structuredClone(variant === 'scripted' ? SCRIPTED : SIMULATED)
}
