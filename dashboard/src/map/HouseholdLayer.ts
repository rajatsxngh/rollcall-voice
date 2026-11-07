import L from 'leaflet'
import type { Household, StatusMeta } from '../api/types'
import type { HouseholdChange, RunStore } from '../store/runStore'

const PULSE_MS = 1600
const PULSE_FRAME_MS = 33 // ~30 fps is plenty for a ring

/** Dot radius in pixels for a zoom level (about 3.5 px at the default zoom 12). */
export function radiusForZoom(zoom: number): number {
  return Math.max(2, Math.min(7, 3.5 + (zoom - 12)))
}

/**
 * All households as circle markers on one shared canvas, restyled in place when their status
 * changes. Calling homes get an expanding ring drawn on a second canvas underneath, so the
 * animation never redraws the 2,000 dots. Driven imperatively: React would be far too slow here.
 */
export class HouseholdLayer {
  private readonly dots: L.Canvas
  private readonly pulse: L.Canvas
  private readonly markers = new Map<number, L.CircleMarker>()
  private readonly halos = new Map<number, L.CircleMarker>()
  private radius: number
  private raf = 0
  private lastFrame = 0
  private readonly unsubscribe: () => void

  constructor(
    private readonly map: L.Map,
    private readonly store: RunStore,
    private readonly onSelect: (id: number) => void,
    private outline: string,
  ) {
    if (!map.getPane('rc-pulse')) map.createPane('rc-pulse').style.zIndex = '440'
    if (!map.getPane('rc-dots')) map.createPane('rc-dots').style.zIndex = '450'
    this.dots = L.canvas({ pane: 'rc-dots', padding: 0.25, tolerance: 4 })
    this.pulse = L.canvas({ pane: 'rc-pulse', padding: 0.25 })
    this.radius = radiusForZoom(map.getZoom())
    map.on('zoomend', this.onZoom)
    this.unsubscribe = store.subscribeHouseholds(this.onChange)
    this.syncAll()
  }

  setOutline(outline: string): void {
    if (outline === this.outline) return
    this.outline = outline
    this.syncAll()
  }

  destroy(): void {
    this.unsubscribe()
    this.map.off('zoomend', this.onZoom)
    cancelAnimationFrame(this.raf)
    this.raf = 0
    for (const m of this.markers.values()) m.remove()
    for (const m of this.halos.values()) m.remove()
    this.markers.clear()
    this.halos.clear()
    this.map.removeLayer(this.dots)
    this.map.removeLayer(this.pulse)
  }

  private onZoom = (): void => {
    const r = radiusForZoom(this.map.getZoom())
    if (r === this.radius) return
    this.radius = r
    this.syncAll()
  }

  private onChange = (change: HouseholdChange): void => {
    if (change.all) {
      this.syncAll()
      return
    }
    for (const id of change.ids) this.update(id, true)
    this.animate()
  }

  private syncAll(): void {
    const seen = new Set<number>()
    for (const h of this.store.households.values()) {
      seen.add(h.id)
      this.update(h.id, false)
    }
    for (const [id, m] of this.markers) {
      if (seen.has(id)) continue
      m.remove()
      this.markers.delete(id)
      this.removeHalo(id)
    }
    this.animate()
  }

  private update(id: number, raise: boolean): void {
    const h = this.store.households.get(id)
    if (!h) return
    const s = this.store.statusOf(h.status)
    const dim = this.store.warningArea != null && !h.affected
    const style = this.styleFor(s, dim)
    let marker = this.markers.get(id)
    if (!marker) {
      marker = L.circleMarker([h.lat, h.lng], { ...style, renderer: this.dots, bubblingMouseEvents: false })
      marker.on('click', () => this.onSelect(id))
      marker.addTo(this.map)
      this.markers.set(id, marker)
    } else {
      const at = marker.getLatLng()
      if (at.lat !== h.lat || at.lng !== h.lng) marker.setLatLng([h.lat, h.lng])
      marker.setStyle(style)
      if (raise) marker.bringToFront() // the latest change draws on top of its neighbors
    }
    if (s.style === 'pulse' && !dim) this.addHalo(h, s)
    else this.removeHalo(id)
  }

  /** Map style per /meta status style: solid, pulse (dot plus ring), hollow, ring. */
  private styleFor(s: StatusMeta, dim: boolean): L.CircleMarkerOptions {
    const fade = dim ? 0.3 : 1
    const r = this.radius
    switch (s.style) {
      case 'hollow':
        return { radius: r, stroke: true, color: s.color, weight: 1.75, opacity: fade, fill: false }
      case 'ring':
        return {
          radius: r + 0.5,
          stroke: true,
          color: this.store.ringColor,
          weight: 2,
          opacity: fade,
          fill: true,
          fillColor: s.color,
          fillOpacity: fade,
        }
      default:
        return {
          radius: r,
          stroke: true,
          color: this.outline,
          weight: 0.75,
          opacity: 0.8 * fade,
          fill: true,
          fillColor: s.color,
          fillOpacity: 0.95 * fade,
        }
    }
  }

  private addHalo(h: Household, s: StatusMeta): void {
    if (this.halos.has(h.id)) return
    const halo = L.circleMarker([h.lat, h.lng], {
      renderer: this.pulse,
      interactive: false,
      radius: this.radius,
      stroke: true,
      color: s.color,
      weight: 2,
      opacity: 0,
      fill: true,
      fillColor: s.color,
      fillOpacity: 0,
    })
    halo.addTo(this.map)
    this.halos.set(h.id, halo)
  }

  private removeHalo(id: number): void {
    const halo = this.halos.get(id)
    if (!halo) return
    halo.remove()
    this.halos.delete(id)
  }

  private animate(): void {
    if (this.halos.size && !this.raf) this.raf = requestAnimationFrame(this.frame)
  }

  private frame = (t: number): void => {
    this.raf = 0
    if (!this.halos.size) return
    if (t - this.lastFrame >= PULSE_FRAME_MS) {
      this.lastFrame = t
      const r = this.radius
      const grow = r * 1.8 + 5
      for (const [id, halo] of this.halos) {
        const p = ((t + id * 211) % PULSE_MS) / PULSE_MS // staggered so the city shimmers
        const fade = 1 - p
        halo.options.opacity = 0.95 * fade
        halo.options.fillOpacity = 0.3 * fade
        halo.setRadius(r + 1 + p * grow)
      }
    }
    this.raf = requestAnimationFrame(this.frame)
  }
}
