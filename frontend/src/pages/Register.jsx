import { useState } from 'react'
import { Link } from 'react-router-dom'
import { UserPlus } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import { Alert } from '../components/ui'
import { VerifyEmailCard } from './VerifyEmail'

export default function Register() {
  const [form, setForm] = useState({ username: '', email: '', full_name: '', password: '', manager_username: '' })
  const [error, setError] = useState('')
  const [sent, setSent] = useState(null)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      const res = await api.post('/auth/register', { ...form, manager_username: form.manager_username || null })
      setSent({ username: form.username, data: res.data })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <AuthShell>
      {sent ? (
        <VerifyEmailCard username={sent.username} initial={sent.data} />
      ) : (
        <form className="auth-card" onSubmit={submit}>
          <div>
            <h2>Create an account</h2>
            <p className="muted">We’ll email you a code to confirm your address. After that, an administrator approves the account.</p>
          </div>
          {error && <Alert type="error">{error}</Alert>}
          <div className="field">
            <label>Full name <span className="req" aria-hidden="true">*</span></label>
            <input value={form.full_name} onChange={set('full_name')} required />
          </div>
          <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div>
              <label>Username <span className="req" aria-hidden="true">*</span></label>
              <input
                value={form.username}
                onChange={set('username')}
                required
                minLength={3}
                maxLength={50}
                pattern="[A-Za-z0-9_.\-]+"
                title="Letters, numbers, dot (.), dash (-) and underscore (_), no spaces"
                autoComplete="username"
              />
              <span className="hint">Letters, numbers, . - and _ (no spaces)</span>
            </div>
            <div>
              <label>Password <span className="req" aria-hidden="true">*</span></label>
              <input type="password" value={form.password} onChange={set('password')} minLength={8} maxLength={100} required autoComplete="new-password" />
              <span className="hint">At least 8 characters</span>
            </div>
          </div>
          <div className="field">
            <label>Email <span className="req" aria-hidden="true">*</span></label>
            <input type="email" value={form.email} onChange={set('email')} required />
          </div>
          <div className="field">
            <label>Manager’s username (optional)</label>
            <input value={form.manager_username} onChange={set('manager_username')} placeholder="Who you report to" />
            <span className="hint">Your manager, and anyone above them, can view your expenses but cannot change them.</span>
          </div>
          <button className="btn block">
            <UserPlus size={16} /> Create account
          </button>
          <p className="muted" style={{ textAlign: 'center', margin: 0 }}>
            Already have an account? <Link to="/login">Sign in</Link>
          </p>
        </form>
      )}
    </AuthShell>
  )
}
