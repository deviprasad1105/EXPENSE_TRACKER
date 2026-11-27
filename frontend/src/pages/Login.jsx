import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ArrowLeft, KeyRound, Lock, LogIn, Mail, ShieldCheck } from 'lucide-react'
import { errorMessage } from '../api'
import { useAuth } from '../auth'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'
import Logo from '../components/Logo'
import OtpField from '../components/OtpField'
import PasswordInput from '../components/PasswordInput'

export default function Login() {
  const { user, login, completeMfa, completeEmailCode, resendEmailCode, setNewPassword } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', password: '' })
  const [error, setError] = useState('')
  const [needsCode, setNeedsCode] = useState(false)
  const [busy, setBusy] = useState(false)
  // Two-step sign-in: the ticket from the password step, and the code from the authenticator app
  const [mfaToken, setMfaToken] = useState(null)
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  // Signed in with a temporary password: a new password must be chosen
  const [passwordToken, setPasswordToken] = useState(null)
  const [newPw, setNewPw] = useState({ password: '', confirm: '' })
  // The admin turned on "email code at sign-in": { token, hint, expiresIn } after the earlier steps
  const [emailStep, setEmailStep] = useState(null)

  // A step returned { mfaToken } or { passwordToken }, or signed in
  const goNext = (res) => {
    if (res.emailToken) {
      setMfaToken(null)
      setEmailStep({ token: res.emailToken, hint: res.emailHint, expiresIn: res.expiresIn })
      setCode('')
    } else if (res.mfaToken) {
      setMfaToken(res.mfaToken)
      setCode('')
    } else if (res.passwordToken) {
      setMfaToken(null)
      setPasswordToken(res.passwordToken)
      setNewPw({ password: '', confirm: '' })
    } else {
      navigate('/')
    }
  }

  if (user) return <Navigate to="/" replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setNeedsCode(false)
    try {
      goNext(await login(form.username, form.password))
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
      goNext(await completeMfa(mfaToken, code.trim()))
    } catch (err) {
      setError(errorMessage(err))
      // The ticket expired or the account was blocked: start again from the password
      if ([429].includes(err?.response?.status) || /expired/i.test(errorMessage(err))) setMfaToken(null)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  const submitNewPassword = async (e) => {
    e.preventDefault()
    if (newPw.password !== newPw.confirm) return setError('The new passwords do not match')
    setBusy(true)
    setError('')
    try {
      goNext(await setNewPassword(passwordToken, newPw.password))
    } catch (err) {
      setError(errorMessage(err))
      // The temporary password expired meanwhile: start again
      if (err?.response?.status === 401) setPasswordToken(null)
    } finally {
      setBusy(false)
    }
  }

  const submitEmailCode = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      goNext(await completeEmailCode(emailStep.token, code.trim()))
    } catch (err) {
      setError(errorMessage(err))
      if ([401, 429].includes(err?.response?.status)) setEmailStep(null)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  const resendCode = async () => {
    const r = await resendEmailCode(emailStep.token)
    setEmailStep({ token: r.emailToken, hint: r.emailHint, expiresIn: r.expiresIn })
    return { expires_in_seconds: r.expiresIn, message: `We emailed a new code to ${r.emailHint}.` }
  }

  const backToPassword = () => {
    setEmailStep(null)
    setMfaToken(null)
    setPasswordToken(null)
    setError('')
    setUseRecovery(false)
    setForm({ ...form, password: '' })
  }

  if (passwordToken) {
    return (
      <AuthShell>
        <form className="auth-card" onSubmit={submitNewPassword}>
          <div>
            <KeyRound size={36} color="var(--primary)" />
            <h2>Choose a new password</h2>
            <p className="muted">
              You signed in with a temporary password. Choose a new password to continue. You’ll be signed out on your other devices.
            </p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <div className="field">
            <label htmlFor="new-password">New password</label>
            <PasswordInput id="new-password" value={newPw.password} onChange={(e) => setNewPw({ ...newPw, password: e.target.value })}
              minLength={8} maxLength={100} required autoFocus autoComplete="new-password" />
            <span className="muted small">At least 8 characters.</span>
          </div>
          <div className="field">
            <label htmlFor="confirm-password">Confirm new password</label>
            <PasswordInput id="confirm-password" value={newPw.confirm} onChange={(e) => setNewPw({ ...newPw, confirm: e.target.value })}
              minLength={8} maxLength={100} required autoComplete="new-password" />
          </div>
          <button className="btn block" disabled={busy}>
            <KeyRound size={16} /> {busy ? 'Saving…' : 'Save and sign in'}
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

  if (emailStep) {
    return (
      <AuthShell>
        <form className="auth-card" onSubmit={submitEmailCode}>
          <div>
            <Mail size={36} color="var(--primary)" />
            <h2>Check your email</h2>
            <p className="muted">We emailed a 6-digit code to {emailStep.hint}. Enter it to finish signing in.</p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <OtpField
            value={code}
            onChange={setCode}
            send={resendCode}
            initial={{ expires_in_seconds: emailStep.expiresIn, message: `Code sent to ${emailStep.hint}.` }}
          />
          <button className="btn block" disabled={busy || code.length !== 6}>
            <ShieldCheck size={16} /> {busy ? 'Checking…' : 'Verify and sign in'}
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
        <div className="auth-head">
          <Logo size={64} />
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
          <div className="icon-input">
            <span className="icon-input-icon"><Mail size={20} /></span>
            <input id="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required autoFocus autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="Your username or email address" />
          </div>
        </div>
        <div className="field">
          <div className="row-between">
            <label htmlFor="password">Password <span className="req" aria-hidden="true">*</span></label>
            <Link to="/forgot-password">
              Forgot password?
            </Link>
          </div>
          <PasswordInput id="password" icon={<Lock size={20} />} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required autoComplete="current-password" placeholder="Your password" />
        </div>
        <button className="btn block" disabled={busy}>
          <LogIn size={18} /> {busy ? 'Signing in…' : 'Sign In'}
        </button>
        <p className="auth-divider">
          <span>
            Don’t have an account? <Link to="/register">Create one</Link>
          </span>
        </p>
      </form>
    </AuthShell>
  )
}
