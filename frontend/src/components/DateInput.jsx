import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { DatePicker } from '@mui/x-date-pickers/DatePicker'
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider'
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs'
import { CalendarDays } from 'lucide-react'

const ISO = 'YYYY-MM-DD'
const CalendarIcon = () => <CalendarDays size={17} strokeWidth={2} />
const parse = (v) => (v ? dayjs(v) : null) // 'YYYY-MM-DD' is ISO: no format plugin needed

/**
 * A date field with a calendar, in place of <input type="date">. Same props: `value` / `min` / `max` as
 * 'YYYY-MM-DD' strings, and `onChange` gets an input-like event ({ target: { value } }), with '' when cleared.
 * A date being typed is passed on only once it is a whole, valid date inside min / max.
 */
export default function DateInput({ value, min, max, onChange, required, disabled, id, className, style, ...rest }) {
  const [draft, setDraft] = useState(parse(value))
  useEffect(() => setDraft(parse(value)), [value])
  const minDate = parse(min)
  const maxDate = parse(max)

  const emit = (v) => onChange?.({ target: { value: v } })
  const change = (d) => {
    setDraft(d)
    if (!d) return emit('')
    if (!d.isValid() || (minDate && d.isBefore(minDate, 'day')) || (maxDate && d.isAfter(maxDate, 'day'))) return
    const next = d.format(ISO)
    if (next !== value) emit(next)
  }

  return (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <DatePicker
        value={draft}
        onChange={change}
        minDate={minDate || undefined}
        maxDate={maxDate || undefined}
        disabled={disabled}
        format="DD MMM YYYY"
        slots={{ openPickerIcon: CalendarIcon }}
        slotProps={{
          textField: {
            id,
            required,
            size: 'small',
            className: `date-input ${className || ''}`,
            style,
            inputProps: { 'aria-label': rest['aria-label'] },
          },
          openPickerButton: { disableRipple: true, className: 'date-btn', 'aria-label': 'Open calendar' },
          popper: { className: 'date-popper' },
          desktopPaper: { className: 'date-paper' },
          mobilePaper: { className: 'date-paper' },
          actionBar: { actions: required ? ['today'] : ['today', 'clear'] },
        }}
      />
    </LocalizationProvider>
  )
}
