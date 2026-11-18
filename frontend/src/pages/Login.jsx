import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { errorMessage } from '../api'
import { useAuth } from '../auth'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'
import PasswordInput from '../components/PasswordInput'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', password: '' })
  const [error, setError] = useState('')
  const [needsCode, setNeedsCode] = useState(false)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setNeedsCode(false)
    try {
      await login(form.username, form.password)
      navigate('/')
    } catch (err) {
      setError(errorMessage(err))
      setNeedsCode(err?.response?.status === 403 && /confirm your email/i.test(errorMessage(err)))
    } finally {
      setBusy(false)
    }
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
