import type { GeoJsonObject } from 'geojson'
import L from 'leaflet'
import { useEffect, useRef } from 'react'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, ZoomControl, useMap } from 'react-leaflet'
import { fmtInt } from '../lib/format'
import { THEMES, TILE_ATTRIBUTION, type Theme } from '../lib/theme'
import { store, useRunSnapshot } from '../live'
import { HouseholdLayer } from './HouseholdLayer'
import { Legend } from './Legend'

const RICHARDSON: [number, number] = [32.96, -96.71]

export function MapView({ theme }: { theme: Theme }) {
  const snap = useRunSnapshot()
  const colors = THEMES[theme]

  return (
    <div className="absolute inset-0">
      <MapContainer
        center={RICHARDSON}
        zoom={12}
        minZoom={9}
        maxZoom={18}
        zoomSnap={0.25}
        zoomControl={false}
        className="h-full w-full"
      >
        <TileLayer key={theme} url={colors.tiles} attribution={TILE_ATTRIBUTION} maxZoom={20} />
        <ZoomControl position="topright" />
        {snap.warningArea && (
          <GeoJSON
            key={`${snap.warningVersion}-${theme}`}
            data={snap.warningArea as unknown as GeoJsonObject}
            interactive={false}
            style={{
              color: colors.accent,
              weight: 2.5,
              opacity: 0.95,
              dashArray: '7 6',
              fillColor: colors.accent,
              fillOpacity: theme === 'dark' ? 0.1 : 0.08,
            }}
          />
        )}
        <Households outline={colors.dotOutline} />
        <FitToCity />
        <SelectedHousehold color={colors.accent} />
      </MapContainer>

      {snap.meta && snap.ready && <Legend snap={snap} accent={colors.accent} />}
      {!snap.ready && <LoadingCard conn={snap.conn} error={snap.error} />}
    </div>
  )
}

/** Mounts the canvas layer for the 2,000 dots. */
function Households({ outline }: { outline: string }) {
  const map = useMap()
  const layer = useRef<HouseholdLayer | null>(null)
  useEffect(() => {
    const created = new HouseholdLayer(map, store, (id) => store.select(id), outline)
    layer.current = created
    return () => {
      created.destroy()
      layer.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])
  useEffect(() => layer.current?.setOutline(outline), [outline])
  return null
}

/**
 * Once, when the first snapshot arrives: frame every household (about Richardson at zoom 12,
 * a little closer or wider depending on the screen). Later reloads of /state leave the view alone.
 */
function FitToCity() {
  const map = useMap()
  const { ready } = useRunSnapshot()
  const done = useRef(false)
  useEffect(() => {
    if (!ready || done.current || store.households.size === 0) return
    done.current = true
    const bounds = L.latLngBounds([])
    for (const h of store.households.values()) bounds.extend([h.lat, h.lng])
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 13, animate: false })
  }, [ready, map])
  return null
}

/**
 * Marks the clicked dot with a ring; its details open in the household panel
 * (panels/HouseholdDetail.tsx) from snapshot.selectedId.
 */
function SelectedHousehold({ color }: { color: string }) {
  const snap = useRunSnapshot()
  const id = snap.selectedId
  const h = id != null ? store.households.get(id) : undefined
  if (id == null || !h) return null
  return (
    <CircleMarker
      key={id}
      center={[h.lat, h.lng]}
      radius={11}
      interactive={false}
      pathOptions={{ color, weight: 3, opacity: 1, fill: false }}
    />
  )
}

function LoadingCard({ conn, error }: { conn: string; error: string | null }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-[1000] grid place-items-center">
      <div className="pointer-events-auto max-w-md rounded-xl border border-line bg-panel/95 px-6 py-5 shadow-2xl backdrop-blur">
        <div className="text-[15px] font-semibold text-ink">
          {conn === 'offline' ? 'Waiting for the backend' : 'Loading the city'}
        </div>
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
          {error ?? `Fetching ${fmtInt(2000)} households and the event stream.`}
          {conn === 'offline' && ' Retrying automatically.'}
        </p>
      </div>
    </div>
  )
}
