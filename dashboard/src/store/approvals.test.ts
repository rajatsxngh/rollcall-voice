import { describe, expect, it } from 'vitest'
import type { ApprovalItem, RunEvent } from '../api/types'
import { ApprovalsStore, sortApprovals } from './approvals'

function item(action_id: number, extra: Partial<ApprovalItem> = {}): ApprovalItem {
  return {
    action_id,
    household_id: action_id,
    name: `H${action_id}`,
    address: `${action_id} Oak St`,
    type: 'hotel_voucher',
    cost: 180,
    reason: 'no power',
    urgency_status: 'needs_help',
    created_at: `2026-11-07T23:${String(10 + action_id).padStart(2, '0')}:00.000Z`,
    ...extra,
  }
}

let seq = 0
function ev(type: string, household_id: number | null, payload: Record<string, unknown> = {}): RunEvent {
  return { id: ++seq, time: '2026-11-07T23:30:00.000Z', type, household_id, payload }
}

/** A loader whose responses the test hands out one at a time. */
function controlledLoader() {
  const calls: ((items: ApprovalItem[]) => void)[] = []
  const loader = () => new Promise<ApprovalItem[]>((resolve) => calls.push(resolve))
  return { loader, calls }
}

const ids = (s: ApprovalsStore) => s.getSnapshot().items.map((i) => i.action_id)

describe('ApprovalsStore (docs/API.md "GET /approvals")', () => {
  it('loads, adds on approval.requested and removes on approval.decided', async () => {
    const homes = new Map([[5, { name: 'Maria Lopez', address: '1412 Oak St', status: 'urgent' }]])
    const store = new ApprovalsStore(async () => [item(1), item(2)], (id) => homes.get(id))
    await store.load()
    expect(ids(store)).toEqual([1, 2])

    store.applyEvent(ev('approval.requested', 5, { action_id: 9, type: 'deliver_generator', cost: 800, reason: 'oxygen', text: '…' }))
    // Life-safety (urgent household) goes first; name and address come from the run store.
    expect(ids(store)).toEqual([9, 1, 2])
    expect(store.getSnapshot().items[0]).toMatchObject({
      household_id: 5,
      name: 'Maria Lopez',
      address: '1412 Oak St',
      type: 'deliver_generator',
      cost: 800,
      urgency_status: 'urgent',
    })

    store.applyEvent(ev('approval.requested', 5, { action_id: 9, type: 'deliver_generator' })) // duplicate
    expect(ids(store)).toEqual([9, 1, 2])

    store.applyEvent(ev('approval.decided', 1, { action_id: 1, approved: true }))
    store.applyEvent(ev('approval.decided', 9, { action_id: 9, approved: false }))
    expect(ids(store)).toEqual([2])
    store.applyEvent(ev('approval.decided', 7, { action_id: 77, approved: false })) // unknown: no-op
    expect(ids(store)).toEqual([2])
  })

  it('clears and reloads on run.reset', async () => {
    const { loader, calls } = controlledLoader()
    const store = new ApprovalsStore(loader)
    const first = store.load()
    calls[0]([item(1), item(2)])
    await first
    expect(ids(store)).toEqual([1, 2])

    store.applyEvent(ev('run.reset', null))
    expect(ids(store)).toEqual([]) // cleared at once
    expect(calls).toHaveLength(2) // and reloaded
    calls[1]([item(3)])
    await new Promise((r) => setTimeout(r, 0))
    expect(ids(store)).toEqual([3])
  })

  it('keeps events that arrive during a load, and ignores a stale load', async () => {
    const { loader, calls } = controlledLoader()
    const store = new ApprovalsStore(loader)
    const stale = store.load()
    const fresh = store.load()
    store.applyEvent(ev('approval.decided', 1, { action_id: 1 })) // decided while the GET is in flight
    store.applyEvent(ev('approval.requested', 4, { action_id: 4, type: 'hotel_voucher' }))
    calls[1]([item(1), item(2)]) // the response still lists #1
    await fresh
    calls[0]([item(8)]) // the older request answers last: dropped
    await stale
    expect(ids(store)).toEqual([2, 4])
    expect(store.getSnapshot().loaded).toBe(true)
  })

  it('reports a failed load (e.g. 404 before M8) without losing items', async () => {
    let fail = false
    const store = new ApprovalsStore(async () => {
      if (fail) throw new Error('GET /approvals failed (404)')
      return [item(1)]
    })
    await store.load()
    fail = true
    await store.load()
    expect(store.getSnapshot()).toMatchObject({ loaded: true, error: 'GET /approvals failed (404)' })
    expect(ids(store)).toEqual([1])
  })

  it('sorts life-safety first, then oldest first', () => {
    const sorted = sortApprovals([
      item(1),
      item(2, { urgency_status: 'emergency' }),
      item(3, { created_at: '2026-11-07T23:00:00.000Z' }),
      item(4, { urgency_status: 'urgent', created_at: '2026-11-07T23:01:00.000Z' }),
    ])
    expect(sorted.map((i) => i.action_id)).toEqual([4, 2, 3, 1])
  })
})
