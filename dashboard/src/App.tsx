import { useEffect } from 'react'
import { TopBar } from './components/TopBar'
import type { Theme } from './lib/theme'
import { usePersistentState } from './lib/usePersistentState'
import { sync } from './live'
import { MapView } from './map/MapView'
import { CountersPanel } from './panels/CountersPanel'
import { FeedPanel } from './panels/FeedPanel'
import { HouseholdDetailPanel } from './panels/HouseholdDetail'
import { QueuePanel } from './panels/QueuePanel'

export default function App() {
  const [theme, setTheme] = usePersistentState<Theme>('rc.theme', 'dark')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    sync.start()
    return () => sync.stop()
  }, [])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg text-ink">
      <TopBar theme={theme} onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
      <LiveView theme={theme} />
    </div>
  )
}

/**
 * The live run: the map is the hero, the sidebar stacks panels top to bottom.
 * Later milestones slot in here without a rewrite:
 * - M6: a live call transcript panel and the P1 handoff card, between counters and feed.
 * - M8: the approvals / escalations panel (QueuePanel) between counters and feed, and the
 *   household detail card over the left of the map, opened by store.select(id).
 * - M9: a results / test-mode view rendered in place of <LiveView />, switched from the top bar.
 */
function LiveView({ theme }: { theme: Theme }) {
  return (
    <div className="flex min-h-0 flex-1">
      <main className="relative min-w-0 flex-1">
        <MapView theme={theme} />
        <HouseholdDetailPanel />
      </main>
      <aside className="flex w-[clamp(360px,30vw,460px)] shrink-0 flex-col border-l border-line bg-panel">
        <CountersPanel />
        <QueuePanel />
        <FeedPanel />
      </aside>
    </div>
  )
}
