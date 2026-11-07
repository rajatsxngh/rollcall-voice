import type { CSSProperties } from 'react'
import type { StatusStyle } from '../api/types'

/** The map's dot, in HTML: solid, pulse (dot plus ring), hollow (outline) or ring (red ring). */
export function StatusDot({
  status,
  ringColor,
  size = 10,
}: {
  status: { color: string; style: StatusStyle }
  ringColor: string
  size?: number
}) {
  const style: CSSProperties = { width: size, height: size }
  if (status.style === 'hollow') {
    style.border = `2px solid ${status.color}`
  } else if (status.style === 'ring') {
    style.background = status.color
    style.boxShadow = `0 0 0 2px ${ringColor}`
  } else {
    style.background = status.color
  }
  return (
    <span className="relative inline-block shrink-0 rounded-full" style={style} aria-hidden>
      {status.style === 'pulse' && (
        <span className="rc-ping absolute inset-0 rounded-full" style={{ border: `1.5px solid ${status.color}` }} />
      )}
    </span>
  )
}
