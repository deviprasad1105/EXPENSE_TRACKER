import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { MailCheck, ShieldCheck } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import OtpField from '../components/OtpField'
import { Alert } from '../components/ui'

/** Enter the emailed sign-up code. Shown right after registering, and from the sign-in page for unconfirmed accounts. */
export function VerifyEmailCard({ username: initialUsername = '', initial }) {
  const [username, setUsername] = useState(initialUsername)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      setDone((await api.post('/auth/verify-email', { username: username.trim(), code })).data.message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="auth-card">
        <MailCheck size={40} color="var(--primary)" />
        <h2>Email confirmed</h2>
        <p className="muted">{done}</p>
        <Link to="/login" className="btn block">
          Back to sign in
        </Link>
      </div>
    )
  }

  return (
    <form className="auth-card" onSubmit={submit}>
      <div>
        <h2>Confirm your email</h2>
        <p className="muted">Enter the 6-digit code we emailed you. An admin can approve your account only after this.</p>
      </div>
      {error && <Alert type="error">{error}</Alert>}
      {!initialUsername && (
        <div className="field">
          <label>Username <span className="req" aria-hidden="true">*</span></label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
        </div>
      )}
      <OtpField
        value={code}
        onChange={setCode}
        initial={initial}
        send={async () => {
          if (!username.trim()) throw new Error('Enter your username first')
          return (await api.post('/auth/resend-email-code', { username: username.trim() })).data
        }}
      />
      <button className="btn block" disabled={busy}>
        <ShieldCheck size={16} /> {busy ? 'Checking…' : 'Confirm email'}
      </button>
      <p className="muted" style={{ textAlign: 'center', margin: 0 }}>
        Didn’t get it? Check your spam folder, or send a new code. <Link to="/login">Sign in</Link>
      </p>
    </form>
  )
}

export default function VerifyEmail() {
  const [params] = useSearchParams()
  return (
    <AuthShell>
      <VerifyEmailCard username={params.get('username') || ''} />
    </AuthShell>
  )
}
