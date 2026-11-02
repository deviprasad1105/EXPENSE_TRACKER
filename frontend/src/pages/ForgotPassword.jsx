import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, MailCheck, Send } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'

export default function ForgotPassword() {
  const [username, setUsername] = useState('')
  const [sent, setSent] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      setSent((await api.post('/auth/forgot-password', { username: username.trim() })).data.message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell>
      {sent ? (
        <div className="auth-card">
          <MailCheck size={40} color="var(--primary)" />
          <h2>Check your email</h2>
          <p className="muted">{sent}</p>
          <p className="muted small">You can reset your password at most 2 times in 15 days.</p>
          <Link to="/login" className="btn secondary block">
            <ArrowLeft size={16} /> Back to sign in
          </Link>
        </div>
      ) : (
        <form className="auth-card" onSubmit={submit}>
          <div>
            <h2>Forgot your password?</h2>
            <p className="muted">Enter your username. We’ll email you a temporary password and a link to choose a new one.</p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <div className="field">
            <label htmlFor="username">Username</label>
            <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus autoComplete="username" />
          </div>
          <button className="btn block" disabled={busy}>
            <Send size={16} /> {busy ? 'Sending…' : 'Send reset email'}
          </button>
          <p className="muted" style={{ textAlign: 'center', margin: 0 }}>
            Remembered it? <Link to="/login">Back to sign in</Link>
          </p>
        </form>
      )}
    </AuthShell>
  )
}
