import { StatusDot } from '../components/StatusDot'
import { fmtInt } from '../lib/format'
import { store } from '../live'
import type { UiSnapshot } from '../store/runStore'

/** Map key, built from /meta statuses, with how many homes are in each right now. */
export function Legend({ snap, accent }: { snap: UiSnapshot; accent: string }) {
  if (!snap.meta) return null
  const scoped = snap.warningArea != null
  return (
    <div className="absolute right-3 bottom-7 z-[500] w-[228px] rounded-xl border border-line bg-panel/90 px-4 py-3.5 shadow-xl backdrop-blur short:py-3">
      {/* Bottom right: the storm track runs SW to NE, so that corner of the map stays empty. */}
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">Households</span>
        <span className="text-[11px] text-faint">{scoped ? 'in warning area' : 'all'}</span>
      </div>
      <ul className="space-y-[7px] short:space-y-[5px]">
        {snap.meta.statuses.map((s) => (
          <li key={s.value} className="flex items-center gap-2.5 text-[13.5px]">
            <span className="grid w-3.5 place-items-center">
              <StatusDot status={s} ringColor={store.ringColor} size={s.style === 'ring' ? 9 : 10} />
            </span>
            <span className="flex-1 text-ink">{s.label}</span>
            <span className="tabular-nums text-muted">{fmtInt(snap.statusCounts[s.value] ?? 0)}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex items-center gap-2.5 border-t border-line pt-3 text-[13.5px] text-ink">
        <span
          className="inline-block h-3 w-3.5 rounded-[3px]"
          style={{ border: `2px dashed ${accent}`, background: `color-mix(in srgb, ${accent} 18%, transparent)` }}
          aria-hidden
        />
        Warning area
      </div>
    </div>
  )
}
