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
