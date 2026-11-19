/** Repeating schedules shared by subscriptions and bills (Bills & Subscriptions page). */
export const FREQUENCIES = [
  ['WEEKLY', 'Weekly'],
  ['MONTHLY', 'Monthly'],
  ['QUARTERLY', 'Quarterly'],
  ['HALF_YEARLY', 'Half-yearly'],
  ['YEARLY', 'Yearly'],
  ['CUSTOM', 'Custom'],
]

export const UNITS = [
  ['DAY', 'day(s)'],
  ['WEEK', 'week(s)'],
  ['MONTH', 'month(s)'],
  ['YEAR', 'year(s)'],
]

export const freqLabel = (s) => {
  if (s.frequency !== 'CUSTOM') return FREQUENCIES.find(([k]) => k === s.frequency)?.[1] || s.frequency
  const unit = UNITS.find(([k]) => k === s.interval_unit)?.[1].replace('(s)', s.interval_count === 1 ? '' : 's')
  return `Every ${s.interval_count} ${unit}`
}

export const dueLabel = (days) => (days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`)

export const fmtDate = (d) =>
  d ? new Date(d + 'T00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'
