import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtInt } from '../lib/format'
import { buildCurveChart, cleanCurve, nearestIndex, valueAt, type ChartBox } from './model'

const HEIGHT = 340
const BOX: Omit<ChartBox, 'width' | 'height'> = { left: 56, right: 112, top: 18, bottom: 50 }

/**
 * Urgent homes with help on the way, over run minutes, for both runs. RollCall wears the
 * accent, list order a neutral gray; a dashed line marks every true-urgent home.
 */
export function CurveChart({
  rollcall,
  listOrder,
  trueUrgent,
}: {
  rollcall: [number, number][]
  listOrder: [number, number][]
  trueUrgent: number
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(720)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(320, Math.round(entry.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const rr = useMemo(() => cleanCurve(rollcall), [rollcall])
  const lo = useMemo(() => cleanCurve(listOrder), [listOrder])
  const box: ChartBox = { ...BOX, width, height: HEIGHT }
  const chart = useMemo(() => buildCurveChart([rr, lo], { ...BOX, width, height: HEIGHT }, trueUrgent), [rr, lo, width, trueUrgent])
  const plotBottom = HEIGHT - box.bottom
  const plotRight = width - box.right

  if (!rr.length && !lo.length) {
    return <p className="py-10 text-center text-[14px] text-muted">No curve in these results.</p>
  }

  const hoverMinute = hover != null ? chart.minutes[hover] : null
  const hv = hoverMinute != null ? { rr: valueAt(rr, hoverMinute), lo: valueAt(lo, hoverMinute) } : null
  const endLabel = (curve: [number, number][]) => (curve.length ? curve[curve.length - 1] : null)
  const rrEnd = endLabel(rr)
  const loEnd = endLabel(lo)
  // Keep the two end labels from overlapping when the runs finish close together.
  let rrLabelY = rrEnd ? chart.y(rrEnd[1]) : 0
  let loLabelY = loEnd ? chart.y(loEnd[1]) : 0
  if (rrEnd && loEnd && Math.abs(rrLabelY - loLabelY) < 18) {
    rrLabelY = Math.min(rrLabelY, loLabelY) - 9
    loLabelY = rrLabelY + 18
  }

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const minute = ((e.clientX - rect.left) / Math.max(1, rect.width)) * chart.xMax
    setHover(nearestIndex(chart.minutes, minute))
  }

  return (
    <div ref={wrap} className="relative w-full">
      <svg width={width} height={HEIGHT} role="img" aria-label="Urgent homes with help on the way over minutes since the alert, RollCall versus list order" className="block">
        {/* Grid and y axis */}
        {chart.yTicks.map((t) => (
          <g key={`y${t}`}>
            <line x1={box.left} x2={plotRight} y1={chart.y(t)} y2={chart.y(t)} stroke="var(--line)" strokeWidth={1} />
            <text x={box.left - 10} y={chart.y(t)} dy="0.32em" textAnchor="end" className="fill-faint text-[12px] tabular-nums">
              {fmtInt(t)}
            </text>
          </g>
        ))}
        {chart.xTicks.map((t) => (
          <text key={`x${t}`} x={chart.x(t)} y={plotBottom + 20} textAnchor="middle" className="fill-faint text-[12px] tabular-nums">
            {t}
          </text>
        ))}
        <line x1={box.left} x2={plotRight} y1={plotBottom} y2={plotBottom} stroke="var(--faint)" strokeWidth={1} />
        <text x={(box.left + plotRight) / 2} y={HEIGHT - 6} textAnchor="middle" className="fill-muted text-[12.5px]">
          Minutes since the alert
        </text>
        <text transform={`translate(14 ${(box.top + plotBottom) / 2}) rotate(-90)`} textAnchor="middle" className="fill-muted text-[12.5px]">
          Urgent homes with help on the way
        </text>

        {/* Every true-urgent home */}
        {trueUrgent > 0 && (
          <g>
            <line x1={box.left} x2={plotRight} y1={chart.y(trueUrgent)} y2={chart.y(trueUrgent)} stroke="var(--muted)" strokeWidth={1} strokeDasharray="4 4" />
            <text x={box.left + 6} y={chart.y(trueUrgent) - 6} className="fill-muted text-[12px]">
              All {fmtInt(trueUrgent)} truly urgent homes
            </text>
          </g>
        )}

        {/* Series: list order under RollCall */}
        <path d={chart.paths[1]} fill="none" stroke="var(--faint)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        <path d={chart.paths[0]} fill="none" stroke="var(--accent)" strokeWidth={3} strokeLinejoin="round" strokeLinecap="round" />

        {/* Direct labels at line ends (text stays in ink colors) */}
        {loEnd && (
          <text x={chart.x(loEnd[0]) + 8} y={loLabelY} dy="0.32em" className="fill-muted text-[13px] font-medium">
            List order
          </text>
        )}
        {rrEnd && (
          <text x={chart.x(rrEnd[0]) + 8} y={rrLabelY} dy="0.32em" className="fill-ink text-[13px] font-semibold">
            RollCall
          </text>
        )}

        {/* Hover crosshair */}
        {hoverMinute != null && hv && (
          <g pointerEvents="none">
            <line x1={chart.x(hoverMinute)} x2={chart.x(hoverMinute)} y1={box.top} y2={plotBottom} stroke="var(--muted)" strokeWidth={1} />
            {hv.lo != null && <circle cx={chart.x(hoverMinute)} cy={chart.y(hv.lo)} r={4.5} fill="var(--faint)" stroke="var(--panel)" strokeWidth={2} />}
            {hv.rr != null && <circle cx={chart.x(hoverMinute)} cy={chart.y(hv.rr)} r={5} fill="var(--accent)" stroke="var(--panel)" strokeWidth={2} />}
          </g>
        )}
        <rect
          x={box.left}
          y={box.top}
          width={Math.max(0, plotRight - box.left)}
          height={Math.max(0, plotBottom - box.top)}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>

      {hoverMinute != null && hv && (
        <div
          className="pointer-events-none absolute top-3 rounded-lg border border-line bg-raised px-3 py-2 text-[13px] shadow-lg"
          style={chart.x(hoverMinute) > width / 2 ? { right: width - chart.x(hoverMinute) + 12 } : { left: chart.x(hoverMinute) + 12 }}
        >
          <div className="mb-1 font-semibold tabular-nums text-ink">Minute {hoverMinute}</div>
          <TipRow swatch="var(--accent)" label="RollCall" value={hv.rr} />
          <TipRow swatch="var(--faint)" label="List order" value={hv.lo} />
        </div>
      )}
    </div>
  )
}

function TipRow({ swatch, label, value }: { swatch: string; label: string; value: number | null }) {
  return (
    <div className="flex items-center gap-2 text-muted">
      <span className="h-[3px] w-3.5 rounded-full" style={{ background: swatch }} aria-hidden />
      <span>{label}</span>
      <span className="ml-auto pl-4 font-semibold tabular-nums text-ink">{value == null ? '—' : fmtInt(value)}</span>
    </div>
  )
}

export function CurveLegend() {
  return (
    <div className="flex items-center gap-5 text-[13px] text-muted">
      <span className="flex items-center gap-2">
        <span className="h-[3px] w-5 rounded-full bg-accent" aria-hidden />
        <span className="font-medium text-ink">RollCall</span> (risk-ranked)
      </span>
      <span className="flex items-center gap-2">
        <span className="h-[3px] w-5 rounded-full bg-faint" aria-hidden />
        List order
      </span>
      <span className="flex items-center gap-2">
        <span className="w-5 border-t border-dashed border-muted" aria-hidden />
        All urgent
      </span>
    </div>
  )
}
