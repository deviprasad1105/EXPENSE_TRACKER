import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ArrowLeft, LogIn, ShieldCheck } from 'lucide-react'
import { errorMessage } from '../api'
import { useAuth } from '../auth'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'
import PasswordInput from '../components/PasswordInput'

export default function Login() {
  const { user, login, completeMfa } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', password: '' })
  const [error, setError] = useState('')
  const [needsCode, setNeedsCode] = useState(false)
  const [busy, setBusy] = useState(false)
  // Two-step sign-in: the ticket from the password step, and the code from the authenticator app
  const [mfaToken, setMfaToken] = useState(null)
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setNeedsCode(false)
    try {
      const res = await login(form.username, form.password)
      if (res.mfaToken) {
        setMfaToken(res.mfaToken)
        setCode('')
        return
      }
      navigate('/')
    } catch (err) {
      setError(errorMessage(err))
      setNeedsCode(err?.response?.status === 403 && /confirm your email/i.test(errorMessage(err)))
    } finally {
      setBusy(false)
    }
  }

  const submitCode = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await completeMfa(mfaToken, code.trim())
      navigate('/')
    } catch (err) {
      setError(errorMessage(err))
      // The ticket expired or the account was blocked: start again from the password
      if ([429].includes(err?.response?.status) || /expired/i.test(errorMessage(err))) setMfaToken(null)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  const backToPassword = () => {
    setMfaToken(null)
    setError('')
    setUseRecovery(false)
    setForm({ ...form, password: '' })
  }

  if (mfaToken) {
    return (
      <AuthShell>
        <form className="auth-card" onSubmit={submitCode}>
          <div>
            <ShieldCheck size={36} color="var(--primary)" />
            <h2>Two-step sign-in</h2>
            <p className="muted">
              {useRecovery
                ? 'Enter one of the recovery codes you saved when you turned on two-step sign-in. Each code works once.'
                : 'Open your authenticator app and enter the 6-digit code for Expense Tracker.'}
            </p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <div className="field">
            <label htmlFor="mfa-code">{useRecovery ? 'Recovery code' : 'Code'}</label>
            {useRecovery ? (
              <input id="mfa-code" key="recovery" value={code} onChange={(e) => setCode(e.target.value)} required autoFocus
                autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="xxxxx-xxxxx" maxLength={20} />
            ) : (
              <input id="mfa-code" key="totp" className="mfa-code-input" value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} required autoFocus
                inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" placeholder="123456" maxLength={6} />
            )}
          </div>
          <button className="btn block" disabled={busy || (!useRecovery && code.length !== 6)}>
            <ShieldCheck size={16} /> {busy ? 'Checking…' : 'Verify and sign in'}
          </button>
          <button type="button" className="btn ghost block" onClick={() => { setUseRecovery(!useRecovery); setCode(''); setError('') }}>
            {useRecovery ? 'Use the code from my app instead' : 'Lost your phone? Use a recovery code'}
          </button>
          <p className="muted small" style={{ textAlign: 'center', margin: 0 }}>
            <button type="button" className="link-button" onClick={backToPassword}>
              <ArrowLeft size={14} /> Back to sign in
            </button>
          </p>
        </form>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <form className="auth-card" onSubmit={submit}>
        <div>
          <h2>Welcome back</h2>
          <p className="muted">Sign in to your account to continue.</p>
        </div>
        {error && (
          <Alert type="error">
            {error}
            {needsCode && (
              <>
                {' '}
                <Link to={`/verify-email?username=${encodeURIComponent(form.username.trim())}`}>Enter the code</Link>
              </>
            )}
          </Alert>
        )}
        <div className="field">
          <label htmlFor="username">Username or email <span className="req" aria-hidden="true">*</span></label>
          <input id="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required autoFocus autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="Your username or email address" />
        </div>
        <div className="field">
          <div className="row-between">
            <label htmlFor="password">Password <span className="req" aria-hidden="true">*</span></label>
            <Link to="/forgot-password" className="small">
              Forgot password?
            </Link>
          </div>
          <PasswordInput id="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required autoComplete="current-password" />
        </div>
        <button className="btn block" disabled={busy}>
          <LogIn size={16} /> {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="muted" style={{ textAlign: 'center', margin: 0 }}>
          Don’t have an account? <Link to="/register">Create one</Link>
        </p>
        <p className="muted small" style={{ textAlign: 'center', margin: 0 }}>
          Deleted your account? <Link to="/recover-account">Restore it or erase its data</Link>
        </p>
      </form>
    </AuthShell>
  )
}
