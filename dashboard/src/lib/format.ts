const formatters = new Map<string, Intl.DateTimeFormat>()

function formatter(tz: string, withSeconds: boolean): Intl.DateTimeFormat {
  const key = `${tz}|${withSeconds}`
  let f = formatters.get(key)
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        hour: 'numeric',
        minute: '2-digit',
        second: withSeconds ? '2-digit' : undefined,
      })
    } catch {
      f = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', second: withSeconds ? '2-digit' : undefined })
    }
    formatters.set(key, f)
  }
  return f
}

/** "6:01:02 PM" in the display time zone (Central). */
export function formatTime(iso: string | null | undefined, tz: string, withSeconds = true): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : formatter(tz, withSeconds).format(d)
}

/** Short zone name for a moment, e.g. "CST" or "CDT". */
export function zoneAbbrev(tz: string, iso?: string | null): string {
  try {
    const d = iso ? new Date(iso) : new Date()
    const part = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
      .formatToParts(d)
      .find((p) => p.type === 'timeZoneName')
    return part?.value ?? ''
  } catch {
    return ''
  }
}

/** Run time between two ISO times: "42s", "14 min", "1 h 5 min"; null if unknown. */
export function formatElapsed(from: string | null | undefined, to: string | null | undefined): string | null {
  if (!from || !to) return null
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (!Number.isFinite(ms)) return null
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

const integer = new Intl.NumberFormat('en-US')
export const fmtInt = (n: number): string => integer.format(n)

export function percent(part: number, whole: number): string {
  if (!whole) return '0%'
  return `${Math.round((part / whole) * 100)}%`
}
