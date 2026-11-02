import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, KeyRound } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'

export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [info, setInfo] = useState(null)
  const [invalid, setInvalid] = useState('')
  const [form, setForm] = useState({ temp_password: '', new_password: '', confirm: '' })
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  useEffect(() => {
    if (!token) return setInvalid('This reset link is missing its token. Open the link from the email again.')
    api
      .get(`/auth/reset-password/${encodeURIComponent(token)}`)
      .then((r) => setInfo(r.data))
      .catch((e) => setInvalid(errorMessage(e)))
  }, [token])

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if (form.new_password !== form.confirm) return setError('New passwords do not match')
    try {
      const res = await api.post('/auth/reset-password', { token, temp_password: form.temp_password.trim(), new_password: form.new_password })
      setDone(res.data.message)
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  return (
    <AuthShell>
      <div className="auth-card">
        {done ? (
          <>
            <CheckCircle2 size={40} color="var(--success)" />
            <h2>Password changed</h2>
            <p className="muted">{done}</p>
            <Link to="/login" className="btn block">
              Sign in
            </Link>
          </>
        ) : invalid ? (
          <>
            <h2>Link not valid</h2>
            <Alert type="error">{invalid}</Alert>
            <Link to="/forgot-password" className="btn block">
              Request a new link
            </Link>
          </>
        ) : !info ? (
          <p className="muted">Checking your link…</p>
        ) : (
          <form onSubmit={submit} className="auth-card" style={{ maxWidth: 'none' }}>
            <div>
              <h2>Choose a new password</h2>
              <p className="muted">
                For <b>@{info.username}</b>. Enter the temporary password from the email, then your new password.
              </p>
            </div>
            {error && <Alert type="error">{error}</Alert>}
            <div className="field">
              <label>Temporary password</label>
              <input value={form.temp_password} onChange={set('temp_password')} required autoFocus autoComplete="one-time-code" />
            </div>
            <div className="field">
              <label>New password</label>
              <input type="password" value={form.new_password} onChange={set('new_password')} minLength={8} required autoComplete="new-password" />
            </div>
            <div className="field">
              <label>Confirm new password</label>
              <input type="password" value={form.confirm} onChange={set('confirm')} minLength={8} required autoComplete="new-password" />
            </div>
            <button className="btn block">
              <KeyRound size={16} /> Change password
            </button>
            <p className="muted small" style={{ margin: 0 }}>
              The link expires at {new Date(info.expires_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}.
            </p>
          </form>
        )}
      </div>
    </AuthShell>
  )
}
