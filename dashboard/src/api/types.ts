// Shapes from docs/API.md. Keep this file in step with that contract.

export type StatusStyle = 'solid' | 'pulse' | 'hollow' | 'ring'

export interface StatusMeta {
  value: string
  label: string
  color: string
  style: StatusStyle
  reached: boolean
  covered: boolean
}

export interface UrgencyMeta {
  value: string
  label: string
  status: string
  color: string
}

export interface Meta {
  display_tz: string
  statuses: StatusMeta[]
  urgencies: UrgencyMeta[]
  event_types: { value: string; doc: string }[]
  resource_types: string[]
  action_types: string[]
  help_action_types: string[]
}

export interface Household {
  id: number
  name: string
  address: string
  lat: number
  lng: number
  status: string
  risk_score: number
  wave: number | null
  affected: boolean
  live: boolean
}

export interface Resource {
  id: number
  type: string
  name: string
  lat: number
  lng: number
  capacity: number
  used: number
}

export type AreaGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }

export interface WarningArea {
  type: 'Feature'
  properties: Record<string, unknown> | null
  geometry: AreaGeometry
}

export interface Counts {
  households: number
  affected: number
  called: number
  reached: number
  urgent: number
  help_sent: number
  not_reached: number
}

export interface RunState {
  time: string
  last_event_id: number
  warning_area: WarningArea | null
  households: Household[]
  resources: Resource[]
  helped_household_ids: number[]
  counts: Counts
}

/** The event envelope. `payload` varies by type (see the Events table in docs/API.md). */
export interface RunEvent {
  id: number
  time: string
  type: string
  household_id: number | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>
}

export interface EventsPage {
  events: RunEvent[]
  last_id: number
}

export interface Health {
  status: string
  last_event_id: number
}

// --- M8: approvals and household detail ----------------------------------------------

/** One action waiting for the coordinator (GET /approvals). */
export interface ApprovalItem {
  action_id: number
  household_id: number
  name: string
  address: string
  type: string
  cost: number | null
  reason: string
  /** The household's status: `emergency` or `urgent` make it life-safety. */
  urgency_status: string | null
  created_at: string
}

export interface ApprovalsList {
  approvals: ApprovalItem[]
}

export interface RiskFactor {
  key: string
  label: string
  points: number
}

export interface HouseholdProfile {
  age: number | null
  lives_alone: boolean | null
  medical_device: string | null
  mobility: string | null
  has_car: boolean | null
  kids_under_5: number | null
  language: string | null
}

export interface HouseholdNeed {
  id: number
  category: string
  details: string
  urgency: string
  created_at: string
}

export interface TranscriptLine {
  role: string
  line: string
}

export interface HouseholdCall {
  id: number | string
  attempt: number
  channel: string
  outcome: string | null
  started_at: string | null
  ended_at: string | null
  summary: string | null
  transcript: TranscriptLine[] | null
}

export interface HouseholdAction {
  id: number
  type: string
  status: string
  cost: number | null
  reason: string | null
  needs_approval: boolean
  resource: string | null
  scheduled_for: string | null
  created_at: string
}

export interface FollowUp {
  step: number
  channel: string
  at: string
}

/** GET /households/{id}. */
export interface HouseholdDetail extends Household {
  risk_factors: RiskFactor[]
  profile: HouseholdProfile
  needs: HouseholdNeed[]
  calls: HouseholdCall[]
  actions: HouseholdAction[]
  next_followup: FollowUp | null
}

// ---- M9: GET /results ----------------------------------------------------------------------

export interface ResultsSource {
  /** "simulated" (Nemotron plays the residents) or "scripted" (answers from the hidden truth). */
  calls: string
  simulated_households: number
  scripted_households: number
  /** "nemotron", "keywords", "rules", ... */
  triage: string
  lines: number
  seed: number | null
  spec_hash: string | null
  // Sent by the backend, not (yet) in docs/API.md:
  voice_model?: string | null
  resident_model?: string | null
  tokens_per_household?: number | null
}

/** One evaluation run (risk-ranked or list order). Minutes are run-clock minutes since the alert. */
export interface ResultsRun {
  affected: number
  calls: number
  coverage_pct: number
  true_urgent: number
  urgent_found: number
  urgent_found_pct: number
  /** True-urgent homes that got help on the way by any route (sent by the backend, not in docs/API.md). */
  true_urgent_helped?: number
  flagged_urgent: number
  false_alarms: number
  false_alarm_pct: number
  median_minutes_to_help: number | null
  p90_minutes_to_help: number | null
  /** null when some true-urgent home never got help. */
  minutes_until_all_urgent_helped: number | null
  avg_call_minutes: number | null
  minutes_total: number | null
  /** [minute, urgent homes with help on the way by then]; both runs share the same minutes. */
  curve: [number, number][]
}

export interface TriageScore {
  urgent_found_pct: number
  false_alarm_pct: number
}

export interface TriageCompare {
  households: number
  nemotron: TriageScore
  keywords: TriageScore
}

export interface TrapResult {
  household_id: number
  persona: string
  true_urgency: string | null
  found_urgency: string | null
  keyword_urgency: string | null
  passed: boolean
  rule: string
  summary: string | null
}

export interface TrapCase {
  household_id: number
  name: string
  persona: string
  quote: string
  keyword_said: string
  nemotron_said: string
  transcript: TranscriptLine[]
}

export interface ResultsTarget {
  name: string
  target: string
  value: string
  met: boolean
}

export interface Results {
  generated_at: string
  source: ResultsSource
  runs: { risk_ranked: ResultsRun; list_order: ResultsRun }
  speedup: { median: number | null; p90: number | null; all: number | null } | null
  triage_compare: TriageCompare | null
  traps: TrapResult[]
  trap_case: TrapCase | null
  latency: { turns: number; p50_ms: number; p90_ms: number } | null
  targets: ResultsTarget[]
  notes: string[]
}
