import { useEffect, useState } from 'react'
import { MailCheck, Send } from 'lucide-react'
import { errorMessage } from '../api'

const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

/**
 * A one-time email code input with a "Send code" button and an expiry countdown.
 * `send` calls the API and resolves to its response data ({ message, expires_in_seconds }).
 * `initial` is that data when a code was already sent (e.g. right after sign-up).
 */
export default function OtpField({ value, onChange, send, initial, label = 'Code from your email' }) {
  const [deadline, setDeadline] = useState(() => (initial?.expires_in_seconds ? Date.now() + initial.expires_in_seconds * 1000 : null))
  const [now, setNow] = useState(Date.now())
  const [note, setNote] = useState(initial ? { type: initial.email_sent === false ? 'error' : 'ok', text: initial.message } : null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!deadline) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [deadline])

  const left = deadline ? Math.max(0, Math.round((deadline - now) / 1000)) : 0

  const sendCode = async () => {
    setBusy(true)
    setNote(null)
    try {
      const data = await send()
      setDeadline(Date.now() + (data.expires_in_seconds || 0) * 1000)
      setNow(Date.now())
      setNote({ type: 'ok', text: data.message })
      onChange('')
    } catch (e) {
      setNote({ type: 'error', text: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="otp-field">
      <label>{label}</label>
      <div className="otp-row">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          title="The 6-digit code from the email"
          placeholder="6-digit code"
          required
        />
        <button type="button" className="btn secondary" onClick={sendCode} disabled={busy}>
          <Send size={14} /> {busy ? 'Sending…' : deadline ? 'Send a new code' : 'Send code'}
        </button>
      </div>
      {note && <span className={`hint ${note.type === 'error' ? 'otp-error' : ''}`}>{note.type === 'ok' && <MailCheck size={12} />} {note.text}</span>}
      {deadline && (
        <span className={`hint ${left ? '' : 'otp-error'}`}>
          {left ? `The code expires in ${clock(left)}` : 'The code has expired. Send a new one.'}
        </span>
      )}
    </div>
  )
}
