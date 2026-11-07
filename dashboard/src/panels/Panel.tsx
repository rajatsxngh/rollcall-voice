import type { ReactNode } from 'react'

/**
 * A sidebar section. Later milestones add theirs (live call, P1 handoff, approvals) by
 * dropping another <Panel> into the sidebar in App.tsx.
 */
export function Panel({
  title,
  aside,
  children,
  className = '',
  bodyClassName = '',
}: {
  title: string
  aside?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={`flex flex-col border-b border-line last:border-b-0 ${className}`}>
      <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-2.5 short:pt-3 short:pb-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">{title}</h2>
        {aside != null && <div className="truncate text-[12px] text-faint">{aside}</div>}
      </header>
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}
