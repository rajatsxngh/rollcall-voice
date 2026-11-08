import { useSyncExternalStore } from 'react'
import { API_BASE, httpTransport } from '../api/transport'
import { fixtureTransport } from '../fixture/fixtureTransport'
import { ApprovalsStore, type ApprovalsSnapshot } from '../store/approvals'
import { RunStore, type UiSnapshot } from '../store/runStore'
import { LiveSync } from './sync'

/** `?fixture` in the URL fakes the backend in the browser (UI development only). */
export const usingFixture = new URLSearchParams(window.location.search).has('fixture')

export const store = new RunStore()
export const transport = usingFixture ? fixtureTransport() : httpTransport(API_BASE)
export const sync = new LiveSync(store, transport)

export function useRunSnapshot(): UiSnapshot {
  return useSyncExternalStore(store.subscribe, store.getSnapshot)
}

/** M8: the coordinator's queue. Reloads with every /state snapshot, then follows the events. */
export const approvals = new ApprovalsStore(
  async () => (await transport.getApprovals()).approvals,
  (id) => store.households.get(id),
)
store.onEvent((e) => approvals.applyEvent(e))
store.onLoad(() => void approvals.load())

export function useApprovals(): ApprovalsSnapshot {
  return useSyncExternalStore(approvals.subscribe, approvals.getSnapshot)
}

// The store and the socket live here. Hot-swapping this module (or the store, sync or transport
// it imports) would leave a second, idle store behind, so in dev those edits reload the page.
if (import.meta.hot) import.meta.hot.accept(() => window.location.reload())
