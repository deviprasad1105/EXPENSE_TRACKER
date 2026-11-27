import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import CheckCircleOutlineRounded from '@mui/icons-material/CheckCircleOutlineRounded'
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded'
import EventNoteOutlined from '@mui/icons-material/EventNoteOutlined'
import MarkChatReadOutlined from '@mui/icons-material/MarkChatReadOutlined'
import SmsOutlined from '@mui/icons-material/SmsOutlined'
import api, { errorMessage, isApp, money, niceDate, todayISO } from '../api'
import { openSettingsLeft, phoneStatus, scanInbox, setPhoneSources, turnOnEverything, unlinkPhone, AutoTrack } from '../autotrack'
import { Alert, Msg, PageHeader, StatTile } from '../components/ui'

const STATUS = {
  CREATED: ['ok', 'Recorded'],
  PENDING: ['info', 'To review'],
  FAILED: ['bad', 'Couldn’t read'],
  BILL: ['warn', 'Bill due'],
  DUPLICATE: ['', 'Already recorded'],
  IGNORED: ['', 'Skipped'],
  DISMISSED: ['', 'Not recorded'],
}
const KINDS = [
  ['EXPENSE', 'Expense'],
  ['INCOME', 'Money received'],
  ['TRANSFER', 'Transfer'],
]
const COUNT_WORDS = { CREATED: 'recorded', PENDING: 'to review', FAILED: 'couldn’t be read', BILL: 'bills', DUPLICATE: 'already recorded', IGNORED: 'skipped', SEEN: 'seen before' }
const summary = (counts) =>
  Object.entries(counts)
    .map(([k, v]) => `${v} ${COUNT_WORDS[k] || k.toLowerCase()}`)
    .join(', ') || 'nothing new'

export default function AutoTracking() {
  const [settings, setSettings] = useState(null)
  const [form, setForm] = useState(null)
  const [messages, setMessages] = useState([])
  const [bills, setBills] = useState([])
  const [accounts, setAccounts] = useState([])
  const [categories, setCategories] = useState([])
  const [phone, setPhone] = useState(null)
  const [filter, setFilter] = useState('all')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    const [s, m, b] = await Promise.all([api.get('/autotrack/settings'), api.get('/autotrack/messages', { params: { limit: 300 } }), api.get('/autotrack/bills')])
    setSettings(s.data)
    setForm((f) => f || { enabled: s.data.enabled, mode: s.data.mode, since: s.data.since || '' })
    setMessages(m.data)
    setBills(b.data)
    setPhone(await phoneStatus())
  }, [])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
    api.get('/accounts').then((r) => setAccounts(r.data.filter((a) => a.is_active))).catch(() => {})
    api.get('/categories').then((r) => setCategories(r.data.filter((c) => !c.is_hidden).map((c) => c.name))).catch(() => {})
  }, [load])
  // New messages arrive in the background; back from Android settings, the permissions may have changed
  useEffect(() => {
    const id = setInterval(() => load().catch(() => {}), 60_000)
    const onFocus = () => load().catch(() => {})
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      clearInterval(id)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [load])

  const run = async (label, fn) => {
    setBusy(label)
    setMsg(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    } finally {
      setBusy('')
    }
  }

  const saveSettings = (e) => {
    e?.preventDefault()
    return run('settings', async () => {
      const { data } = await api.put('/autotrack/settings', { ...form, since: form.since || null })
      setForm({ enabled: data.enabled, mode: data.mode, since: data.since || '' })
      if (AutoTrack && phone?.linked) await setPhoneSources({ enabled: data.enabled, sms: phone.smsOn || !phone.notificationsOn, notifications: phone.notificationsOn })
      setMsg({ type: 'ok', text: data.enabled ? 'Auto-tracking is on.' : 'Auto-tracking is off.' })
    })
  }

  const pending = messages.filter((m) => m.status === 'PENDING' || m.status === 'FAILED')
  const ready = pending.filter((m) => m.status === 'PENDING' && !m.reason).length
  const shown = useMemo(
    () => messages.filter((m) => filter === 'all' || (filter === 'recorded' ? m.status === 'CREATED' : !['CREATED', 'PENDING', 'FAILED'].includes(m.status))),
    [messages, filter],
  )
  const recordedMonth = messages.filter((m) => m.status === 'CREATED' && m.txn_date?.slice(0, 7) === todayISO().slice(0, 7))

  return (
    <div>
      <PageHeader title="Auto-tracking" subtitle="Your bank’s SMS and app alerts, turned into expenses, money received and bills due." />
      <Msg msg={msg} />

      <div className="tiles">
        <StatTile hero label="To review" value={pending.length} sub={ready ? `${ready} ready to record` : 'Nothing waiting'} icon={MarkChatReadOutlined} />
        <StatTile label="Recorded this month" value={recordedMonth.length} sub={money(recordedMonth.reduce((t, m) => t + Number(m.amount || 0), 0))} icon={CheckCircleOutlineRounded} />
        <StatTile label="Bills due" value={bills.length} sub={bills[0] ? `Next: ${bills[0].name} ${bills[0].due_date ? niceDate(bills[0].due_date) : ''}` : 'None'} icon={EventNoteOutlined} />
        <StatTile label="Last message" value={settings?.last_received_at ? niceDate(settings.last_received_at, { relative: true }) : '—'} icon={SmsOutlined} />
      </div>

      <div className="grid-2">
        {form && <SettingsCard form={form} setForm={setForm} save={saveSettings} busy={busy === 'settings'} />}
        {isApp ? (
          <PhoneCard phone={phone} settings={settings} form={form} run={run} busy={busy} setMsg={setMsg} />
        ) : (
          <div className="card">
            <h3>On your phone</h3>
            <p className="muted">
              Reading bank SMS needs the Expense Tracker Android app: sign in there and open Auto-tracking. It sends only bank
              messages about money, never personal messages or one-time passwords.
            </p>
            <p className="muted">
              On an iPhone (which doesn’t let apps read SMS), use the forwarding link in <Link to="/settings#upi">Settings → UPI tracker</Link>, or paste a message below.
            </p>
          </div>
        )}
      </div>

      {settings && !settings.enabled && <Alert type="warn">Auto-tracking is off: messages are kept but nothing is recorded. Turn it on above.</Alert>}

      <div className="card">
        <div className="card-head">
          <div>
            <h3>To review</h3>
            <p>Check each one, change anything, then record it. Add an account’s last 4 digits in <Link to="/settings#accounts">Settings → Bank accounts</Link> so it’s found next time.</p>
          </div>
          {ready > 0 && (
            <button className="btn" disabled={!!busy} onClick={() => run('all', async () => setMsg({ type: 'ok', text: `Recorded ${(await api.post('/autotrack/messages/accept-all')).data.recorded}.` }))}>
              Record all ready ({ready})
            </button>
          )}
        </div>
        {!pending.length ? (
          <p className="muted">Nothing to review.</p>
        ) : (
          <div className="review-list">
            {pending.map((m) => (
              <ReviewItem key={m.id} m={m} accounts={accounts} categories={categories} run={run} busy={busy} />
            ))}
          </div>
        )}
      </div>

      {bills.length > 0 && (
        <div className="card">
          <h3>Bills due</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Bill</th>
                  <th>Due</th>
                  <th className="num">Amount</th>
                  <th className="num">Minimum</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {bills.map((b) => (
                  <tr key={b.id}>
                    <td>{b.name}</td>
                    <td>{b.due_date ? niceDate(b.due_date, { relative: true }) : '—'}</td>
                    <td className="num strong">{money(b.amount)}</td>
                    <td className="num">{b.min_due ? money(b.min_due) : '—'}</td>
                    <td className="num">
                      <button className="btn ghost small" disabled={!!busy} onClick={() => run('bill', () => api.post(`/autotrack/messages/${b.id}/dismiss`))}>
                        Paid / hide
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <PasteCard run={run} busy={busy} setMsg={setMsg} />

      <div className="card">
        <div className="card-head">
          <div>
            <h3>Messages received</h3>
            <p>
              Recorded ones are in <Link to="/expenses">Expenses</Link> and <Link to="/funds">Balance</Link>, marked SMS.
            </p>
          </div>
          <div className="segmented">
            {[
              ['all', 'All'],
              ['recorded', 'Recorded'],
              ['skipped', 'Skipped'],
            ].map(([k, label]) => (
              <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Received</th>
                <th>Message</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {!shown.length && (
                <tr>
                  <td colSpan={5} className="muted">
                    No messages yet.
                  </td>
                </tr>
              )}
              {shown.map((m) => (
                <tr key={m.id}>
                  <td className="nowrap">{niceDate(m.received_at, { relative: true })}</td>
                  <td>
                    <div className="strong">{m.name || m.party || m.sender || '—'}</div>
                    <div className="hint clamp-2" title={m.body}>
                      {m.body}
                    </div>
                  </td>
                  <td className={`num ${m.direction === 'CREDIT' ? 'net-pos' : ''}`}>{m.amount ? money(m.amount) : '—'}</td>
                  <td>
                    <span className={`badge ${STATUS[m.status]?.[0] || ''}`}>{STATUS[m.status]?.[1] || m.status}</span>
                    {m.reason && <div className="hint">{m.reason}</div>}
                  </td>
                  <td className="num">
                    <button
                      className="btn ghost small"
                      title="Remove from this list"
                      aria-label="Remove from this list"
                      onClick={() => window.confirm('Remove this message from the list? Anything recorded from it stays.') && run('delete', () => api.delete(`/autotrack/messages/${m.id}`))}
                    >
                      <DeleteOutlineRounded fontSize="small" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function SettingsCard({ form, setForm, save, busy }) {
  return (
    <form className="card" onSubmit={save}>
      <div className="row-between">
        <h3>Auto-tracking</h3>
        <label className="switch" title="Turn auto-tracking on or off">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} aria-label="Auto-tracking on" />
          <span />
        </label>
      </div>
      <div className="field">
        <label>When a transaction is found</label>
        <div className="segmented">
          {[
            ['REVIEW', 'Ask me first'],
            ['AUTO', 'Record automatically'],
          ].map(([k, label]) => (
            <button type="button" key={k} className={form.mode === k ? 'on' : ''} onClick={() => setForm({ ...form, mode: k })}>
              {label}
            </button>
          ))}
        </div>
        <span className="hint">
          {form.mode === 'AUTO'
            ? 'Recorded straight away when the account is known and nothing like it is already in your records; anything unsure still waits for you.'
            : 'Every transaction waits in “To review” until you record it.'}
        </span>
      </div>
      <div className="field">
        <label htmlFor="at-since">Only transactions from</label>
        <input id="at-since" type="date" max={todayISO()} value={form.since} onChange={(e) => setForm({ ...form, since: e.target.value })} />
        <span className="hint">Older ones are skipped, so records you already have (typed in or imported) aren’t added twice.</span>
      </div>
      <div className="form-actions">
        <button className="btn" disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  )
}

function PhoneCard({ phone, settings, form, run, busy, setMsg }) {
  const [scan, setScan] = useState({ from: '', to: todayISO(), progress: '' })
  const from = scan.from || settings?.since || todayISO()
  if (!phone) return <div className="card muted">Checking this phone…</div>
  const restricted = phone.sdk >= 33
  const sources = (over) => ({ enabled: settings?.enabled ?? form?.enabled ?? true, sms: phone.smsOn, notifications: phone.notificationsOn, ...over })
  const allGood = phone.linked && phone.sms && phone.postNotifications && (!phone.notificationsOn || phone.notificationAccess) && phone.batteryFree

  const readPast = async (since, until) => {
    setScan((s) => ({ ...s, progress: 'Reading your SMS…' }))
    const r = await scanInbox(since, until, (done, total) => setScan((s) => ({ ...s, progress: `Sending ${done} of ${total}…` })))
    setScan((s) => ({ ...s, progress: '' }))
    return r
  }

  const doScan = () =>
    run('scan', async () => {
      const r = await readPast(from, scan.to)
      setMsg({
        type: 'ok',
        text: `Looked at ${r.scanned} SMS, ${r.found} from banks: ${summary(r.counts)}.${r.limited ? ' Only the first 5,000 were taken: scan a shorter period for the rest.' : ''}`,
      })
    })

  // One tap: turn it on, link the phone, ask for everything, read this period's messages, then open the two
  // Android screens that can't be asked from a dialog
  const turnOn = () =>
    run('setup', async () => {
      const status = await turnOnEverything()
      let found = ''
      if (status.sms) {
        const { data } = await api.get('/autotrack/settings')
        const r = await readPast(data.since || todayISO(), todayISO())
        found = r.found ? ` Read ${r.found} bank message(s) already on the phone: ${summary(r.counts)}.` : ''
      }
      if (!status.sms) {
        setMsg({ type: 'warn', text: 'Android didn’t allow reading SMS. Follow the steps below, then tap “Turn on” again.' })
        return
      }
      const left = !status.notificationAccess || !status.batteryFree
      setMsg({
        type: 'ok',
        text: `Auto-tracking is on.${found}${left ? ' Last step: Android opens its settings now. Allow Expense Tracker there, then come back.' : ''}`,
      })
      if (left) await openSettingsLeft(status)
    })

  return (
    <div className="card">
      <div className="row-between">
        <h3>This phone</h3>
        {phone.linked && <span className={`badge ${allGood ? 'ok' : 'warn'}`}>{allGood ? 'Tracking automatically' : 'Needs attention'}</span>}
      </div>
      {!allGood && (
        <>
          <p className="muted">
            {phone.linked
              ? 'Something Android needs is still off. Tap below and allow everything it asks for.'
              : 'One tap sets everything up: your bank SMS and bank-app alerts are recorded automatically, even when the app is closed. Only bank messages about money are sent; personal messages and one-time passwords never leave the phone.'}
          </p>
          <button className="btn block" disabled={!!busy} onClick={turnOn}>
            <SmsOutlined fontSize="small" /> {busy === 'setup' ? scan.progress || 'Setting up…' : phone.linked ? 'Fix it' : 'Turn on automatic tracking'}
          </button>
        </>
      )}
      {phone.linked && (
        <div className="phone-steps" style={{ marginTop: 12 }}>
          <Step ok={phone.sms} title="Bank SMS" text={phone.sms ? 'Read as they arrive.' : 'Not allowed yet.'} />
          {!phone.sms && restricted && (
            <Alert type="info">
              Android blocks this for apps installed outside the Play Store until you allow it: open <b>App info</b> → <b>⋮</b> (top right) → <b>Allow restricted settings</b>,
              come back and tap <b>Fix it</b>.{' '}
              <button className="btn ghost small" onClick={() => AutoTrack.openAppSettings()}>
                Open App info
              </button>
            </Alert>
          )}
          <Step ok={phone.notificationsOn && phone.notificationAccess} title="Bank app alerts" text="For banks and UPI apps that alert in their app. Only bank, card and payment apps are read; never chats or email.">
            <label className="switch">
              <input
                type="checkbox"
                checked={phone.notificationsOn}
                onChange={(e) => run('notif', async () => {
                  await setPhoneSources(sources({ notifications: e.target.checked }))
                  if (e.target.checked && !phone.notificationAccess) await AutoTrack.openNotificationAccess()
                })}
                aria-label="Read bank app notifications"
              />
              <span />
            </label>
          </Step>
          <Step ok={phone.postNotifications} title="Tell me what was recorded" text="A notification for each transaction; tap it to change it." />
          <Step ok={phone.batteryFree} title="Works in the background" text={phone.batteryFree ? 'Android lets it send straight away.' : 'Android may delay sending to save battery.'} />
          {phone.queued > 0 && (
            <Step title={`${phone.queued} message(s) waiting to be sent`} text="They go as soon as the phone is online.">
              <button className="btn small" disabled={!!busy} onClick={() => run('flush', () => AutoTrack.flush())}>
                Send now
              </button>
            </Step>
          )}
          {phone.sms && (
            <details className="field">
              <summary>Read older messages</summary>
              <div className="row-between" style={{ justifyContent: 'flex-start', marginTop: 8 }}>
                <input type="date" aria-label="From" value={from} max={scan.to} onChange={(e) => setScan({ ...scan, from: e.target.value })} />
                <span className="muted">to</span>
                <input type="date" aria-label="To" value={scan.to} max={todayISO()} onChange={(e) => setScan({ ...scan, to: e.target.value })} />
                <button className="btn secondary" disabled={!!busy} onClick={doScan}>
                  {busy === 'scan' ? scan.progress || 'Scanning…' : 'Scan'}
                </button>
              </div>
              <span className="hint">Transactions before “Only transactions from” are skipped, and nothing already recorded is added twice.</span>
            </details>
          )}
          <div className="form-actions">
            <button className="btn ghost small" disabled={!!busy} onClick={() => window.confirm('Stop reading messages on this phone?') && run('unlink', () => unlinkPhone({ everywhere: true }))}>
              Turn off on this phone
            </button>
          </div>
        </div>
      )}
      {settings?.device_linked && !phone.linked && <p className="hint">Another phone is linked (…{settings.device_hint}). Turning it on here stops that one.</p>}
    </div>
  )
}

function Step({ ok, title, text, children }) {
  return (
    <div className="phone-step">
      <span className={`badge ${ok === undefined ? '' : ok ? 'ok' : 'warn'}`}>{ok === undefined ? '•' : ok ? 'On' : 'Off'}</span>
      <div className="grow">
        <div className="strong">{title}</div>
        <div className="hint">{text}</div>
      </div>
      {children}
    </div>
  )
}

function ReviewItem({ m, accounts, categories, run, busy }) {
  const [f, setF] = useState({
    suggestion: m.suggestion || (m.direction === 'CREDIT' ? 'INCOME' : 'EXPENSE'),
    name: m.name || m.party || '',
    category: m.category || '',
    account_id: m.account_id || '',
    to_account_id: m.to_account_id || '',
    amount: m.amount || '',
    txn_date: m.txn_date || todayISO(),
  })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const accept = (e) => {
    e.preventDefault()
    run(`accept-${m.id}`, () =>
      api.post(`/autotrack/messages/${m.id}/accept`, {
        ...f,
        category: f.suggestion === 'EXPENSE' ? f.category || 'General' : null,
        account_id: f.account_id ? Number(f.account_id) : null,
        to_account_id: f.suggestion === 'TRANSFER' && f.to_account_id ? Number(f.to_account_id) : null,
      }),
    )
  }
  const accountOptions = accounts.map((a) => (
    <option key={a.id} value={a.id}>
      {a.name}
      {a.last4 ? ` ••${a.last4}` : ''}
    </option>
  ))
  return (
    <form className="review-item" onSubmit={accept}>
      <div className="review-head">
        <span className={`badge ${STATUS[m.status][0]}`}>{STATUS[m.status][1]}</span>
        <span className="muted small">
          {m.sender || m.source} · {niceDate(m.received_at, { relative: true })}
        </span>
      </div>
      <p className="review-body">{m.body}</p>
      {m.reason && <Alert type={m.status === 'FAILED' || m.reason.includes('may be the same') ? 'warn' : 'info'}>{m.reason}</Alert>}
      <div className="form-grid">
        <div>
          <label>Record as</label>
          <select value={f.suggestion} onChange={set('suggestion')}>
            {KINDS.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>{f.suggestion === 'INCOME' ? 'From / source' : 'Name'}</label>
          <input value={f.name} onChange={set('name')} required maxLength={100} />
        </div>
        {f.suggestion === 'EXPENSE' && (
          <div>
            <label>Category</label>
            <input value={f.category} onChange={set('category')} list={`cats-${m.id}`} maxLength={50} placeholder="General" />
            <datalist id={`cats-${m.id}`}>
              {categories.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>
        )}
        <div>
          <label>{f.suggestion === 'TRANSFER' ? 'From account' : 'Account'}</label>
          <select value={f.account_id} onChange={set('account_id')} required>
            <option value="">Choose…</option>
            {accountOptions}
          </select>
        </div>
        {f.suggestion === 'TRANSFER' && (
          <div>
            <label>To account</label>
            <select value={f.to_account_id} onChange={set('to_account_id')} required>
              <option value="">Choose…</option>
              {accountOptions}
            </select>
          </div>
        )}
        <div>
          <label>Amount</label>
          <input type="number" min="0.01" step="0.01" value={f.amount} onChange={set('amount')} required />
        </div>
        <div>
          <label>Date</label>
          <input type="date" value={f.txn_date} max={todayISO()} onChange={set('txn_date')} required />
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn ghost" disabled={!!busy} onClick={() => run(`dismiss-${m.id}`, () => api.post(`/autotrack/messages/${m.id}/dismiss`))}>
          Don’t record
        </button>
        <button className="btn" disabled={!!busy}>
          {busy === `accept-${m.id}` ? 'Recording…' : 'Record'}
        </button>
      </div>
    </form>
  )
}

function PasteCard({ run, busy, setMsg }) {
  const [text, setText] = useState('')
  const [sender, setSender] = useState('')
  const submit = (e) => {
    e.preventDefault()
    run('paste', async () => {
      const { data } = await api.post('/autotrack/messages', { messages: [{ body: text, sender: sender || null, source: 'PASTE' }] })
      const r = data.results[0]
      setMsg({ type: r && ['CREATED', 'PENDING', 'BILL'].includes(r.status) ? 'ok' : 'warn', text: r ? `${STATUS[r.status][1]}${r.reason ? `: ${r.reason}` : ''}` : 'This message was already received.' })
      setText('')
    })
  }
  return (
    <form className="card" onSubmit={submit}>
      <h3>Add a message by hand</h3>
      <p className="hint">Paste a bank SMS or alert to record it, or to check how it is read.</p>
      <div className="form-grid">
        <div className="span-all">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            maxLength={2000}
            required
            placeholder="e.g. INR 250.00 debited from A/c no. XX1234 on 17-11-26 UPI/P2M/631234567890/SWIGGY. Not you? Call 1800…"
            style={{ width: '100%', resize: 'vertical' }}
          />
        </div>
        <div>
          <label>Sender (optional)</label>
          <input value={sender} onChange={(e) => setSender(e.target.value)} maxLength={80} placeholder="e.g. AX-AXISBK" />
        </div>
        <div className="form-actions">
          <button className="btn" disabled={!text.trim() || !!busy}>
            Read and record
          </button>
        </div>
      </div>
    </form>
  )
}
