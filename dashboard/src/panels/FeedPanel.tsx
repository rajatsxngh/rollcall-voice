import { memo } from 'react'
import { StatusDot } from '../components/StatusDot'
import { formatTime, zoneAbbrev } from '../lib/format'
import { store, useRunSnapshot } from '../live'
import type { FeedItem } from '../store/runStore'
import { Panel } from './Panel'

const TAGS: Record<string, string> = {
  'alert.received': 'Alert',
  'plan.created': 'Plan',
  'need.recorded': 'Need',
  'action.created': 'Action',
  'agent.note': 'Agent',
  'status.update': 'Update',
  'call.started': 'Call',
  'call.ended': 'Call',
  'approval.requested': 'Approval',
  'approval.decided': 'Approval',
  'sms.sent': 'Text',
  'sms.received': 'Text',
  'followup.scheduled': 'Follow-up',
}

/** Lines that mark a turn in the story get a quiet highlight. */
const MILESTONES = new Set(['alert.received', 'plan.created', 'status.update'])

/** One line per event with payload.text, newest first (docs/API.md "Feed rule"). */
export function FeedPanel() {
  const snap = useRunSnapshot()
  const tz = snap.meta?.display_tz ?? 'America/Chicago'
  const zone = zoneAbbrev(tz, snap.runTime)

  return (
    <Panel
      title="Agent feed"
      aside={`Newest first${zone ? ` · ${zone}` : ''}`}
      className="min-h-0 flex-1"
      bodyClassName="min-h-0 flex-1 overflow-y-auto overscroll-contain"
    >
      {snap.feed.length === 0 ? (
        <div className="px-5 py-6 text-[14px] leading-relaxed text-faint">
          {snap.warningArea
            ? 'Waiting for the agent.'
            : 'Quiet for now. When a warning comes in, every plan, decision and booking shows up here in plain words.'}
        </div>
      ) : (
        <ol>
          {snap.feed.map((item) => (
            <FeedRow key={item.id} item={item} time={formatTime(item.time, tz)} />
          ))}
        </ol>
      )}
    </Panel>
  )
}

const FeedRow = memo(function FeedRow({ item, time }: { item: FeedItem; time: string }) {
  let tag = TAGS[item.type] ?? item.type.split('.')[0]
  if (item.type === 'action.created' && item.kind && store.helpTypes.has(item.kind)) tag = 'Help'
  const highlight = MILESTONES.has(item.type)
  return (
    <li
      className={`rc-enter grid grid-cols-[78px_1fr] gap-3 border-b border-line/60 px-5 py-2.5 ${highlight ? 'bg-raised' : ''}`}
    >
      <div className="pt-[1px]">
        <div className="text-[12px] tabular-nums text-muted">{time}</div>
        <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">{tag}</div>
      </div>
      <div className="flex min-w-0 gap-2.5">
        <span className="mt-[6px] grid w-2.5 shrink-0 place-items-center">
          {item.dot && <StatusDot status={item.dot} ringColor={store.ringColor} size={8} />}
        </span>
        <p className={`text-[14px] leading-snug ${highlight ? 'font-medium text-ink' : 'text-ink/90'}`}>{item.text}</p>
      </div>
    </li>
  )
})
