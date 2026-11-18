import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, RotateCcw, Trash2 } from 'lucide-react'
import api, { errorMessage } from '../api'
import AuthShell from '../components/AuthShell'
import OtpField from '../components/OtpField'
import { Alert } from '../components/ui'

const ACTIONS = {
  RESTORE: {
    label: 'Restore my account',
    icon: RotateCcw,
    about: 'Brings your account back with all its data, as it was. Sign in with your old username and password.',
  },
  PURGE: {
    label: 'Delete my data permanently',
    icon: Trash2,
    about: 'Erases the backup kept after your account was deleted. Nobody, not even an administrator, can bring it back.',
  },
}

/** A deleted account's owner restores it, or erases its backup for good, confirming with a code sent to its email. */
export default function RecoverAccount() {
  const [email, setEmail] = useState('')
  const [action, setAction] = useState('RESTORE')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(null)
  const [busy, setBusy] = useState(false)
  // A code is for one action: switching asks for a new one
  const [codeKey, setCodeKey] = useState(0)

  const choose = (a) => {
    if (a === action) return
    setAction(a)
    setCode('')
    setError('')
    setCodeKey((k) => k + 1)
  }

  const submit = async (e) => {
    e.preventDefault()
    if (action === 'PURGE' && !window.confirm('Permanently delete all data of your deleted account? This cannot be undone.')) return
    setBusy(true)
    setError('')
    try {
      setDone((await api.post('/auth/recover/confirm', { email: email.trim(), action, code })).data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthShell>
        <div className="auth-card">
          <CheckCircle2 size={40} color="var(--primary)" />
          <h2>{action === 'RESTORE' ? 'Your account is back' : 'Your data is deleted'}</h2>
          <p className="muted">{done.message}</p>
          <Link to="/login" className="btn block">
            <ArrowLeft size={16} /> Go to sign in
          </Link>
        </div>
      </AuthShell>
    )
  }

  const Icon = ACTIONS[action].icon
  return (
    <AuthShell>
      <form className="auth-card" onSubmit={submit}>
        <div>
          <h2>Deleted your account?</h2>
          <p className="muted">
            When an account is deleted, a backup is kept. Enter the email the account used: we’ll send it a code to bring the
            account back or to erase that backup for good.
          </p>
        </div>
        {error && <Alert type="error">{error}</Alert>}
        <div className="field">
          <label htmlFor="email">Email of the deleted account <span className="req" aria-hidden="true">*</span></label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus autoComplete="email" />
        </div>
        <div className="field">
          <label>What do you want to do?</label>
          <div className="segmented" role="radiogroup">
            {Object.entries(ACTIONS).map(([key, a]) => (
              <button key={key} type="button" role="radio" aria-checked={action === key} className={action === key ? 'on' : ''} onClick={() => choose(key)}>
                <a.icon size={14} /> {key === 'RESTORE' ? 'Restore' : 'Delete permanently'}
              </button>
            ))}
          </div>
          <span className="hint">{ACTIONS[action].about}</span>
        </div>
        {action === 'PURGE' && <Alert type="warn">This can’t be undone.</Alert>}
        <OtpField
          key={codeKey}
          value={code}
          onChange={setCode}
          send={async () => {
            if (!email.trim()) throw new Error('Enter the email first')
            return (await api.post('/auth/recover/send-code', { email: email.trim(), action })).data
          }}
        />
        <button className={`btn block ${action === 'PURGE' ? 'danger' : ''}`} disabled={busy}>
          <Icon size={16} /> {busy ? 'Working…' : ACTIONS[action].label}
        </button>
        <p className="muted" style={{ textAlign: 'center', margin: 0 }}>
          <Link to="/login">Back to sign in</Link>
        </p>
      </form>
    </AuthShell>
  )
}
