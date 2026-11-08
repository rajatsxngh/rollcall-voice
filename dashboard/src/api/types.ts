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
