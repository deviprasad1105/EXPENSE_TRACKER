import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useSearchParams } from 'react-router-dom'
import { MailCheck, RotateCcw, ShieldCheck, Trash2 } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import Modal from '../components/Modal'
import OtpField from '../components/OtpField'
import { Alert } from '../components/ui'

/** Enter the emailed sign-up code. Shown right after registering, and from the sign-in page for unconfirmed accounts. */
export function VerifyEmailCard({ username: initialUsername = '', initial }) {
  const [username, setUsername] = useState(initialUsername)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [busy, setBusy] = useState(false)

  // A deleted account used this email: offered to bring it back or erase it
  const [deleted, setDeleted] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const data = (await api.post('/auth/verify-email', { username: username.trim(), code })).data
      setDone(data.message)
      setDeleted(data.deleted_account || null)
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
        {deleted && (
          <DeletedAccountChoice
            offer={deleted}
            onClose={() => setDeleted(null)}
            onDone={(message) => {
              setDone(message)
              setDeleted(null)
            }}
          />
        )}
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
          <label>Username or email <span className="req" aria-hidden="true">*</span></label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} />
        </div>
      )}
      <OtpField
        value={code}
        onChange={setCode}
        initial={initial}
        send={async () => {
          if (!username.trim()) throw new Error('Enter your username or email first')
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

/** Signed up with the email of a deleted account: bring that account back (this sign-up is cancelled) or erase it. */
function DeletedAccountChoice({ offer, onClose, onDone }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const when = new Date(offer.deleted_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

  const choose = async (action) => {
    if (action === 'PURGE' && !window.confirm(`Permanently delete all data of @${offer.username}? This cannot be undone.`)) return
    setBusy(action)
    setError('')
    try {
      onDone((await api.post('/auth/recover/after-signup', { ticket: offer.ticket, action })).data.message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy('')
    }
  }

  // On the page itself: the sign-in card would otherwise hold the backdrop inside it
  return createPortal(
    <Modal title="You had an account with this email" subtitle={`@${offer.username}, deleted on ${when}`} onClose={onClose}>
      <div className="deleted-choice">
        {error && <Alert type="error">{error}</Alert>}
        <p className="muted">A backup of it is kept. What would you like to do?</p>
        <button className="btn block" disabled={!!busy} onClick={() => choose('RESTORE')}>
          <RotateCcw size={16} /> {busy === 'RESTORE' ? 'Restoring…' : `Restore @${offer.username}`}
        </button>
        <span className="hint">Brings it back with all its data. Sign in with its old password. This new sign-up is cancelled.</span>
        <button className="btn block danger" disabled={!!busy} onClick={() => choose('PURGE')}>
          <Trash2 size={16} /> {busy === 'PURGE' ? 'Erasing…' : 'Erase its data and keep my new account'}
        </button>
        <span className="hint">Deletes the backup for good. Your new account waits for an administrator’s approval.</span>
      </div>
    </Modal>,
    document.body,
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
