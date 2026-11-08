/** Plain-word labels for API codes the dashboard shows. Unknown codes fall back to "Title case". */

const ACTION_TYPES: Record<string, string> = {
  deliver_generator: 'Generator',
  hotel_voucher: 'Hotel voucher',
  dispatch_crew: 'Crew',
  in_person_check: 'In-person check',
  reserve_shelter_bed: 'Shelter bed',
  schedule_adjuster: 'Adjuster visit',
  transfer_to_human: 'Human dispatcher',
  open_claim: 'Insurance claim',
  follow_up: 'Follow-up',
  send_text: 'Text message',
  schedule_followup: 'Follow-up',
}

const CHANNELS: Record<string, string> = {
  call: 'Call',
  text: 'Text',
  sms: 'Text',
  in_person: 'In-person check',
  scripted: 'Scripted call',
  phone: 'Phone call',
  vapi: 'Phone call',
}

export function titleCase(code: string): string {
  const s = code.replace(/_/g, ' ').trim()
  return s ? s[0].toUpperCase() + s.slice(1) : s
}

export const actionLabel = (type: string): string => ACTION_TYPES[type] ?? titleCase(type)
export const channelLabel = (channel: string): string => CHANNELS[channel] ?? titleCase(channel)

let languages: Intl.DisplayNames | null = null
export function languageLabel(code: string | null | undefined): string {
  if (!code) return '—'
  try {
    languages ??= new Intl.DisplayNames(['en'], { type: 'language' })
    return languages.of(code) ?? code
  } catch {
    return code
  }
}

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
export const fmtMoney = (n: number | null | undefined): string => (n == null ? '—' : money.format(n))
