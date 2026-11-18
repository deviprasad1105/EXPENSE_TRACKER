import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { Copy, Download, KeyRound, ShieldCheck, ShieldOff, Smartphone } from 'lucide-react'
import api, { errorMessage, isApp } from '../api'
import { useAuth } from '../auth'
import PasswordInput from './PasswordInput'
import { Msg } from './ui'

const CODE_RE = /\D/g

/**
 * Settings → Security: two-step sign-in with an authenticator app (Google Authenticator, Microsoft Authenticator,
 * Authy…). Steps: password → scan the QR code → enter the first code → save the recovery codes.
 */
export default function MfaSettings() {
  const { updateUser } = useAuth()
  const [state, setState] = useState(null)
  // null | 'password' | 'scan' | 'codes' | 'disable' | 'regenerate'
  const [step, setStep] = useState(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState(null)
  const [qr, setQr] = useState('')
  const [codes, setCodes] = useState([])
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.get('/profile/mfa').then((r) => setState(r.data)).catch(() => {})
  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (!setup) return setQr('')
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 200, errorCorrectionLevel: 'M' }).then(setQr).catch(() => setQr(''))
  }, [setup])

  const reset = (next = null) => {
    setStep(next)
    setPassword('')
    setCode('')
    setMsg(null)
    if (next === null) setSetup(null)
  }

  const run = async (fn) => {
    setBusy(true)
    setMsg(null)
    try {
      await fn()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  const start = (e) => {
    e.preventDefault()
    run(async () => {
      setSetup((await api.post('/profile/mfa/setup', { password })).data)
      setPassword('')
      setCode('')
      setStep('scan')
    })
  }

  const enable = (e) => {
    e.preventDefault()
    run(async () => {
      setCodes((await api.post('/profile/mfa/enable', { code })).data.recovery_codes)
      setSetup(null)
      setStep('codes')
      updateUser({ mfa_enabled: true })
      load()
    })
  }

  const disable = (e) => {
    e.preventDefault()
    run(async () => {
      setState((await api.post('/profile/mfa/disable', { password, code: code.trim() })).data)
      updateUser({ mfa_enabled: false })
      reset()
      setMsg({ type: 'ok', text: 'Two-step sign-in is off. Signing in now needs only your password.' })
    })
  }

  const regenerate = (e) => {
    e.preventDefault()
    run(async () => {
      setCodes((await api.post('/profile/mfa/recovery-codes', { password, code })).data.recovery_codes)
      setPassword('')
      setCode('')
      setStep('codes')
      load()
    })
  }

  const codesText = () =>
    `Expense Tracker recovery codes\nEach code works once, if you can't use your authenticator app.\n\n${codes.join('\n')}\n`

  const copyCodes = async () => {
    try {
      await navigator.clipboard.writeText(codesText())
      setMsg({ type: 'ok', text: 'Recovery codes copied. Paste them somewhere safe, like a password manager.' })
    } catch {
      setMsg({ type: 'error', text: 'Couldn’t copy. Select the codes and copy them by hand.' })
    }
  }

  const downloadCodes = () => {
    const url = URL.createObjectURL(new Blob([codesText()], { type: 'text/plain' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: 'expense-tracker-recovery-codes.txt' })
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const copyKey = async () => {
    try {
      await navigator.clipboard.writeText(setup.secret)
      setMsg({ type: 'ok', text: 'Key copied.' })
    } catch {
      setMsg({ type: 'error', text: 'Couldn’t copy. Type the key in by hand.' })
    }
  }

  const codeInput = (
    <input className="mfa-code-input" value={code} onChange={(e) => setCode(e.target.value.replace(CODE_RE, '').slice(0, 6))}
      inputMode="numeric" autoComplete="one-time-code" placeholder="123456" maxLength={6} required />
  )

  if (!state) return null
  const on = state.enabled

  return (
    <div className="setting-block" id="two-step">
      <div className="setting-row">
        <div>
          <b>
            Two-step sign-in{' '}
            <span className={`badge ${on ? 'ok' : ''}`}>{on ? 'On' : 'Off'}</span>
          </b>
          <p>
            {on
              ? `Signing in needs your password and the 6-digit code from your authenticator app. ${state.recovery_codes_left} recovery code(s) left.`
              : 'Protect your account with a code from an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…) as well as your password.'}
          </p>
        </div>
        {step === null &&
          (on ? (
            <div className="row-actions">
              <button className="btn secondary" onClick={() => reset('regenerate')}>
                <KeyRound size={16} /> New recovery codes
              </button>
              <button className="btn danger" onClick={() => reset('disable')}>
                <ShieldOff size={16} /> Turn off
              </button>
            </div>
          ) : (
            <button className="btn" onClick={() => reset('password')}>
              <ShieldCheck size={16} /> Turn on
            </button>
          ))}
      </div>
      <Msg msg={msg} />

      {step === 'password' && (
        <form className="mfa-panel" onSubmit={start}>
          <p>Enter your password to start.</p>
          <div className="field">
            <label>Password</label>
            <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus autoComplete="current-password" />
          </div>
          <div className="row-actions">
            <button className="btn" disabled={busy}>Continue</button>
            <button type="button" className="btn ghost" onClick={() => reset()}>Cancel</button>
          </div>
        </form>
      )}

      {step === 'scan' && setup && (
        <form className="mfa-panel" onSubmit={enable}>
          <ol className="mfa-steps">
            <li>
              Install an authenticator app if you don’t have one: <b>Google Authenticator</b>, <b>Microsoft Authenticator</b> or <b>Authy</b>.
            </li>
            <li>
              {isApp ? 'Tap the button below to add Expense Tracker to the app on this phone, or scan the code from another phone.' : 'In the app, add an account and scan this QR code.'}
              <div className="mfa-qr-row">
                {qr && <img src={qr} width={180} height={180} alt="QR code to add Expense Tracker to your authenticator app" className="mfa-qr" />}
                <div className="mfa-key">
                  <a className="btn secondary" href={setup.otpauth_uri}>
                    <Smartphone size={16} /> Open in authenticator app
                  </a>
                  <span className="muted small">Can’t scan? Choose “Enter a setup key” in the app and type:</span>
                  <code className="mfa-secret">{setup.secret.match(/.{1,4}/g).join(' ')}</code>
                  <button type="button" className="btn ghost" onClick={copyKey}>
                    <Copy size={16} /> Copy key
                  </button>
                </div>
              </div>
            </li>
            <li>
              Enter the 6-digit code the app shows for Expense Tracker:
              <div className="field" style={{ maxWidth: 200, marginTop: 8 }}>{codeInput}</div>
            </li>
          </ol>
          <div className="row-actions">
            <button className="btn" disabled={busy || code.length !== 6}>
              <ShieldCheck size={16} /> {busy ? 'Checking…' : 'Turn on two-step sign-in'}
            </button>
            <button type="button" className="btn ghost" onClick={() => reset()}>Cancel</button>
          </div>
        </form>
      )}

      {step === 'codes' && (
        <div className="mfa-panel">
          <p>
            <b>Save these recovery codes now.</b> If you lose your phone, each one lets you sign in once instead of a code from the app. They
            won’t be shown again.
          </p>
          <ul className="mfa-recovery">
            {codes.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <div className="row-actions">
            <button className="btn secondary" onClick={copyCodes}>
              <Copy size={16} /> Copy
            </button>
            {!isApp && (
              <button className="btn secondary" onClick={downloadCodes}>
                <Download size={16} /> Download
              </button>
            )}
            <button className="btn" onClick={() => { setCodes([]); reset() }}>
              I’ve saved them
            </button>
          </div>
        </div>
      )}

      {(step === 'disable' || step === 'regenerate') && (
        <form className="mfa-panel" onSubmit={step === 'disable' ? disable : regenerate}>
          <p>
            {step === 'disable'
              ? 'To turn off two-step sign-in, enter your password and a code from your authenticator app (or a recovery code).'
              : 'Your old recovery codes will stop working. Enter your password and a code from your authenticator app.'}
          </p>
          <div className="field">
            <label>Password</label>
            <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus autoComplete="current-password" />
          </div>
          <div className="field" style={{ maxWidth: 240 }}>
            <label>{step === 'disable' ? 'Code or recovery code' : 'Code from the app'}</label>
            {step === 'disable' ? (
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoComplete="one-time-code" autoCapitalize="none"
                autoCorrect="off" spellCheck={false} placeholder="123456" maxLength={20} />
            ) : (
              codeInput
            )}
          </div>
          <div className="row-actions">
            <button className={`btn ${step === 'disable' ? 'danger' : ''}`} disabled={busy}>
              {step === 'disable' ? <><ShieldOff size={16} /> Turn off</> : <><KeyRound size={16} /> Make new codes</>}
            </button>
            <button type="button" className="btn ghost" onClick={() => reset()}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  )
}
