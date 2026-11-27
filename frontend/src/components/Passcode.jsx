import { useCallback, useEffect, useState } from 'react'
import { KeyRound, Lock, LogOut } from 'lucide-react'
import api, { errorMessage } from '../api'
import { isUnlocked, lockApp, markUnlocked, useAuth } from '../auth'
import AuthShell from './AuthShell'
import PasswordInput from './PasswordInput'
import { Alert, Msg } from './ui'

// Locked again after this long in the background
const RELOCK_MS = 5 * 60 * 1000
const digits = (v) => v.replace(/\D/g, '').slice(0, 6)

function PasscodeInput({ id, value, onChange, autoFocus }) {
  return (
    <input
      id={id}
      className="mfa-code-input"
      type="password"
      value={value}
      onChange={(e) => onChange(digits(e.target.value))}
      inputMode="numeric"
      autoComplete="off"
      pattern="\d{4,6}"
      minLength={4}
      maxLength={6}
      placeholder="••••"
      required
      autoFocus={autoFocus}
    />
  )
}

/**
 * When the admin requires a passcode (Admin → Settings), nothing behind sign-in is shown until it is entered: each
 * time the app or site is opened on a device where the user is already signed in, and after 5 minutes in the
 * background. Users without one choose it first. Signing in with the password unlocks without it.
 */
export default function PasscodeGate({ children }) {
  const { user, logout, updateUser } = useAuth()
  const [required, setRequired] = useState(null)
  const [unlocked, setUnlocked] = useState(isUnlocked)
  const [form, setForm] = useState({ code: '', confirm: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const check = useCallback(
    () =>
      api
        .get('/auth/security-policy')
        .then((r) => setRequired(r.data.passcode_required))
        .catch(() => setRequired((cur) => cur ?? false)),
    [],
  )
  useEffect(() => {
    check()
  }, [check, user?.id])

  // Lock again after a while in the background (the app switched away, the laptop left open)
  useEffect(() => {
    let hiddenAt = null
    const onVisibility = () => {
      if (document.hidden) {
        hiddenAt = Date.now()
      } else if (hiddenAt && Date.now() - hiddenAt > RELOCK_MS) {
        lockApp()
        setUnlocked(false)
        check()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [check])

  if (required === null) return <div className="center">Loading…</div>
  if (!required || unlocked || !user) return children

  const open = () => {
    markUnlocked()
    setUnlocked(true)
    setForm({ code: '', confirm: '' })
  }

  const choose = async (e) => {
    e.preventDefault()
    if (form.code !== form.confirm) return setError('The two passcodes don’t match')
    setBusy(true)
    setError('')
    try {
      await api.put('/profile/passcode', { passcode: form.code })
      updateUser({ ...user, passcode_set: true })
      open()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const unlock = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.post('/profile/passcode/verify', { passcode: form.code })
      open()
    } catch (err) {
      setError(errorMessage(err))
      setForm({ code: '', confirm: '' })
    } finally {
      setBusy(false)
    }
  }

  const signOut = (
    <p className="muted small" style={{ textAlign: 'center', margin: 0 }}>
      <button type="button" className="link-button" onClick={logout}>
        <LogOut size={14} /> {user.passcode_set ? 'Forgot it? Sign in with your password' : 'Sign out'}
      </button>
    </p>
  )

  if (!user.passcode_set) {
    return (
      <AuthShell>
        <form className="auth-card" onSubmit={choose}>
          <div>
            <KeyRound size={36} color="var(--primary)" />
            <h2>Choose a passcode</h2>
            <p className="muted">You’ll enter it each time you open Expense Tracker on this device. Use 4 to 6 digits.</p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <div className="field">
            <label htmlFor="pc-new">Passcode</label>
            <PasscodeInput id="pc-new" value={form.code} onChange={(code) => setForm({ ...form, code })} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="pc-confirm">Enter it again</label>
            <PasscodeInput id="pc-confirm" value={form.confirm} onChange={(confirm) => setForm({ ...form, confirm })} />
          </div>
          <button className="btn block" disabled={busy || form.code.length < 4}>
            <KeyRound size={16} /> {busy ? 'Saving…' : 'Save passcode'}
          </button>
          {signOut}
        </form>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <form className="auth-card" onSubmit={unlock}>
        <div>
          <Lock size={36} color="var(--primary)" />
          <h2>Enter your passcode</h2>
          <p className="muted">Signed in as @{user.username}.</p>
        </div>
        {error && <Alert type="error">{error}</Alert>}
        <div className="field">
          <label htmlFor="pc-code">Passcode</label>
          <PasscodeInput id="pc-code" value={form.code} onChange={(code) => setForm({ ...form, code })} autoFocus />
        </div>
        <button className="btn block" disabled={busy || form.code.length < 4}>
          <Lock size={16} /> {busy ? 'Checking…' : 'Unlock'}
        </button>
        {signOut}
      </form>
    </AuthShell>
  )
}

/** Settings → Security: change the passcode (needs the account password). Shown while passcodes are required. */
export function PasscodeSettings() {
  const { user, updateUser } = useAuth()
  const [state, setState] = useState(null)
  const [form, setForm] = useState({ code: '', confirm: '', password: '' })
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get('/profile/passcode').then((r) => setState(r.data)).catch(() => {})
  }, [])
  if (!state?.required) return null

  const save = async (e) => {
    e.preventDefault()
    if (form.code !== form.confirm) return setMsg({ type: 'error', text: 'The two passcodes don’t match' })
    setBusy(true)
    setMsg(null)
    try {
      const { data } = await api.put('/profile/passcode', { passcode: form.code, password: form.password || null })
      setState(data)
      updateUser({ ...user, passcode_set: true })
      setForm({ code: '', confirm: '', password: '' })
      setMsg({ type: 'ok', text: 'Passcode changed.' })
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} style={{ marginTop: 16 }}>
      <h4>Passcode</h4>
      <p className="muted">Asked each time you open Expense Tracker on a device where you’re signed in. Forgot it? Sign out and sign in with your password, then change it here.</p>
      <Msg msg={msg} />
      <div className="form-grid">
        <div>
          <label htmlFor="pcs-new">New passcode</label>
          <PasscodeInput id="pcs-new" value={form.code} onChange={(code) => setForm({ ...form, code })} />
        </div>
        <div>
          <label htmlFor="pcs-confirm">Enter it again</label>
          <PasscodeInput id="pcs-confirm" value={form.confirm} onChange={(confirm) => setForm({ ...form, confirm })} />
        </div>
        {state.set && (
          <div>
            <label htmlFor="pcs-pw">Account password</label>
            <PasswordInput id="pcs-pw" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required autoComplete="current-password" />
          </div>
        )}
      </div>
      <div className="form-actions">
        <button className="btn" disabled={busy || form.code.length < 4}>
          {busy ? 'Saving…' : state.set ? 'Change passcode' : 'Save passcode'}
        </button>
      </div>
    </form>
  )
}
