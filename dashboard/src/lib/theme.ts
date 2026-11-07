export type Theme = 'dark' | 'light'

const env = import.meta.env
const tileOverride = (env.VITE_TILE_URL as string | undefined)?.trim()

/**
 * Basemap: Stadia "Alidade Smooth" (works from localhost without a key; CARTO's free tiles now
 * need one). Override with VITE_TILE_URL (+ VITE_TILE_ATTRIBUTION), e.g. Esri's
 * https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}
 */
export const TILE_ATTRIBUTION =
  (env.VITE_TILE_ATTRIBUTION as string | undefined)?.trim() ||
  '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

/**
 * Colors the map needs in JS. Keep them in step with the CSS tokens in index.css.
 * The accent marks the warning (area, alert, main button); status colors come from /meta only.
 */
export const THEMES: Record<Theme, { tiles: string; accent: string; dotOutline: string }> = {
  dark: {
    tiles: tileOverride || 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png',
    accent: '#a78bfa',
    dotOutline: '#0b0d10',
  },
  light: {
    tiles: tileOverride || 'https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png',
    accent: '#7c3aed',
    dotOutline: '#ffffff',
  },
}
