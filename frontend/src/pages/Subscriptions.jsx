import { useCallback, useEffect, useMemo, useState } from 'react'
import { BellRing, CalendarClock, History, PauseCircle, Pencil, PlayCircle, Plus, Repeat, ScanSearch, Trash2, Wallet } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { hasFeature, useAuth } from '../auth'
import Modal from '../components/Modal'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { Alert, EmptyRow, Msg, PageHeader, StatTile } from '../components/ui'

const FREQUENCIES = [
  ['WEEKLY', 'Weekly'],
  ['MONTHLY', 'Monthly'],
  ['QUARTERLY', 'Quarterly'],
  ['HALF_YEARLY', 'Half-yearly'],
  ['YEARLY', 'Yearly'],
  ['CUSTOM', 'Custom'],
]
const UNITS = [
  ['DAY', 'day(s)'],
  ['WEEK', 'week(s)'],
  ['MONTH', 'month(s)'],
  ['YEAR', 'year(s)'],
]
const PAYMENT_METHODS = {
  GPAY: 'Google Pay',
  PHONEPE: 'PhonePe',
  PAYTM: 'Paytm',
  AMAZONPAY: 'Amazon Pay',
  CRED: 'CRED',
  BHIM: 'BHIM',
  UPI: 'Other UPI app',
  CARD: 'Card',
  NETBANKING: 'Net banking',
  CASH: 'Cash',
  OTHER: 'Other',
}
// Where to cancel the real AutoPay mandate: pausing here only stops tracking and reminders
const CANCEL_HINT = {
  GPAY: 'Google Pay → your profile picture → Autopay',
  PHONEPE: 'PhonePe → profile → Payment settings → AutoPay',
  PAYTM: 'Paytm → UPI & payment settings → Automatic payments',
  AMAZONPAY: 'Amazon Pay → UPI → Manage AutoPay',
  CRED: 'CRED → UPI → AutoPay',
  BHIM: 'BHIM → Mandates',
  UPI: 'your UPI app → AutoPay / Mandates',
  CARD: 'your card app or bank → standing instructions',
  NETBANKING: 'net banking → mandates / standing instructions',
}

const freqLabel = (s) => {
  if (s.frequency !== 'CUSTOM') return FREQUENCIES.find(([k]) => k === s.frequency)?.[1] || s.frequency
  const unit = UNITS.find(([k]) => k === s.interval_unit)?.[1].replace('(s)', s.interval_count === 1 ? '' : 's')
  return `Every ${s.interval_count} ${unit}`
}
const dueLabel = (days) => (days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`)
const fmtDate = (d) => (d ? new Date(d + 'T00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—')

const EMPTY = {
  name: '',
  merchant: '',
  amount: '',
  category: 'Subscriptions',
  frequency: 'MONTHLY',
  interval_count: 1,
  interval_unit: 'MONTH',
  start_date: todayISO(),
  end_date: '',
  payment_method: '',
  payment_ref: '',
  remind_days_before: 1,
  auto_log_expense: true,
  is_active: true,
  note: '',
}

const toPayload = (f) => ({
  ...f,
  amount: Number(f.amount),
  interval_count: Number(f.interval_count) || 1,
  remind_days_before: Number(f.remind_days_before) || 0,
  merchant: f.merchant || null,
  end_date: f.end_date || null,
  payment_method: f.payment_method || null,
  payment_ref: f.payment_ref || null,
  note: f.note || null,
})

export default function Subscriptions() {
  const { params, target, user } = useAuth()
  const [list, setList] = useState([])
  const [summary, setSummary] = useState(null)
  const [show, setShow] = useState('ALL')
  const [selected, setSelected] = useState(new Set())
  const [editing, setEditing] = useState(null)
  const [importing, setImporting] = useState(false)
  const [history, setHistory] = useState(null)
  const [msg, setMsg] = useState(null)
  const writable = target?.writable

  const load = useCallback(async () => {
    const [l, s] = await Promise.all([api.get('/subscriptions', { params }), api.get('/subscriptions/summary', { params })])
    setList(l.data)
    setSummary(s.data)
    setSelected((cur) => new Set([...cur].filter((id) => l.data.some((x) => x.id === id))))
  }, [params])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const run = async (fn, ok) => {
    try {
      const res = await fn()
      setMsg(ok && { type: 'ok', text: typeof ok === 'function' ? ok(res) : ok })
      await load()
      return true
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      return false
    }
  }

  const toggle = (ids, active) =>
    run(
      () => api.post('/subscriptions/toggle', { ids, is_active: active }),
      (res) => {
        const names = res.data.map((s) => s.name).join(', ')
        if (active) return `Resumed: ${names}. Reminders and charge tracking are back on.`
        const hints = [...new Set(res.data.map((s) => CANCEL_HINT[s.payment_method]).filter(Boolean))]
        return (
          `Paused: ${names}. You won't get reminders and no charges are logged.` +
          (hints.length ? ` To stop the actual payment, cancel the AutoPay in ${hints.join('; ')}.` : '')
        )
      },
    ).then((ok) => ok && setSelected(new Set()))

  const remove = (s) =>
    window.confirm(`Delete "${s.name}"? Charges already logged as expenses stay in your expenses.`) &&
    run(() => api.delete(`/subscriptions/${s.id}`), user.role === 'ADMIN' ? 'Subscription deleted. A backup was saved (Administration → Backups).' : 'Subscription deleted')

  const shown = useMemo(
    () => list.filter((s) => show === 'ALL' || (show === 'ACTIVE' ? s.is_active : !s.is_active)),
    [list, show],
  )
  const allSelected = shown.length > 0 && shown.every((s) => selected.has(s.id))
  const pick = (id) =>
    setSelected((cur) => {
      const next = new Set(cur)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  return (
    <div>
      <PageHeader title="Subscriptions" subtitle="Everything that charges you again and again, in one place. Get a reminder before each renewal and pause what you don't use.">
        {writable && (
          <>
            <button className="btn secondary" onClick={() => setImporting(true)}>
              <ScanSearch size={16} /> Find my subscriptions
            </button>
            <button className="btn" onClick={() => setEditing({ ...EMPTY, auto_log_expense: hasFeature(user, 'expense') })}>
              <Plus size={16} /> Add subscription
            </button>
          </>
        )}
      </PageHeader>
      <ReadOnlyNotice />
      <Msg msg={msg} />

      {summary && (
        <div className="tiles">
          <StatTile hero icon={Repeat} label="Per month" value={money(summary.monthly_total)} sub={`${summary.active_count} active subscription${summary.active_count === 1 ? '' : 's'}`} />
          <StatTile icon={Wallet} label="Per year" value={money(summary.yearly_total)} sub="At today's prices" />
          <StatTile icon={CalendarClock} label="Due in the next 7 days" value={money(summary.due_next_7_days)} />
          <StatTile icon={PauseCircle} label="Saved by pausing" value={money(summary.paused_monthly)} sub={`${summary.paused_count} paused · per month`} />
        </div>
      )}

      {summary?.upcoming.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h3>Renewing in the next 30 days</h3>
          </div>
          <ul className="check-list" style={{ maxHeight: 'none' }}>
            {summary.upcoming.map((u) => (
              <li key={u.id}>
                <BellRing size={16} className={u.days_until <= 1 ? '' : 'muted'} style={u.days_until <= 1 ? { color: 'var(--warning)' } : null} />
                <span className="grow">
                  <b>{u.name}</b>
                  <div className="hint">{fmtDate(u.date)}{u.payment_method ? ` · ${PAYMENT_METHODS[u.payment_method]}` : ''}</div>
                </span>
                <span className={`badge ${u.days_until <= 1 ? 'warn' : ''}`}>{dueLabel(u.days_until)}</span>
                <b style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{money(u.amount)}</b>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card flush">
        <div className="toolbar">
          <div className="segmented">
            {[
              ['ALL', `All (${list.length})`],
              ['ACTIVE', `Active (${list.filter((s) => s.is_active).length})`],
              ['PAUSED', `Paused (${list.filter((s) => !s.is_active).length})`],
            ].map(([k, label]) => (
              <button key={k} className={show === k ? 'on' : ''} onClick={() => setShow(k)}>
                {label}
              </button>
            ))}
          </div>
          <span className="spacer" />
          {writable && selected.size > 0 && (
            <>
              <span className="muted">{selected.size} selected</span>
              <button className="btn secondary small" onClick={() => toggle([...selected], false)}>
                <PauseCircle size={14} /> Pause
              </button>
              <button className="btn secondary small" onClick={() => toggle([...selected], true)}>
                <PlayCircle size={14} /> Resume
              </button>
            </>
          )}
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                {writable && (
                  <th style={{ width: 32 }}>
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={allSelected}
                      onChange={() => setSelected(allSelected ? new Set() : new Set(shown.map((s) => s.id)))}
                    />
                  </th>
                )}
                <th>On</th>
                <th>Subscription</th>
                <th className="num">Amount</th>
                <th>Billing</th>
                <th>Next charge</th>
                <th>Paid with</th>
                <th className="num">Charged so far</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.id} style={s.is_active ? null : { opacity: 0.65 }}>
                  {writable && (
                    <td>
                      <input type="checkbox" aria-label={`Select ${s.name}`} checked={selected.has(s.id)} onChange={() => pick(s.id)} />
                    </td>
                  )}
                  <td>
                    <label className="switch" title={s.is_active ? 'Active: click to pause' : 'Paused: click to resume'}>
                      <input type="checkbox" checked={s.is_active} disabled={!writable} onChange={(e) => toggle([s.id], e.target.checked)} />
                      <span />
                    </label>
                  </td>
                  <td>
                    <b>{s.name}</b>
                    <div className="hint">
                      {s.merchant && s.merchant !== s.name ? `${s.merchant} · ` : ''}
                      {s.category}
                      {s.source !== 'MANUAL' && ` · ${s.source === 'SMS' ? 'from SMS' : 'found in expenses'}`}
                    </div>
                  </td>
                  <td className="num strong">
                    {money(s.amount)}
                    {s.frequency !== 'MONTHLY' && <div className="hint">{money(s.monthly_cost)}/month</div>}
                  </td>
                  <td>
                    {freqLabel(s)}
                    <div className="hint">
                      since {fmtDate(s.start_date)}
                      {s.end_date && ` · ends ${fmtDate(s.end_date)}`}
                    </div>
                  </td>
                  <td>
                    {!s.is_active ? (
                      <span className="badge">paused</span>
                    ) : s.next_charge_date ? (
                      <>
                        {fmtDate(s.next_charge_date)}
                        <div className="hint">
                          {dueLabel(s.days_until)}
                          {s.remind_days_before > 0 ? ` · reminder ${s.remind_days_before} day${s.remind_days_before === 1 ? '' : 's'} before` : ' · no reminder'}
                        </div>
                      </>
                    ) : (
                      <span className="badge">ended</span>
                    )}
                  </td>
                  <td>
                    {s.payment_method ? PAYMENT_METHODS[s.payment_method] : <span className="muted">—</span>}
                    {s.payment_ref && <div className="hint">{s.payment_ref}</div>}
                  </td>
                  <td className="num">
                    {money(s.total_charged)}
                    <div className="hint">{s.charge_count} charge{s.charge_count === 1 ? '' : 's'}</div>
                  </td>
                  <td>
                    <div className="actions">
                      <button className="btn ghost small" title="Charge history" onClick={() => setHistory(s)}>
                        <History size={14} />
                      </button>
                      {writable && (
                        <>
                          <button
                            className="btn ghost small"
                            title="Edit"
                            onClick={() => setEditing({ ...EMPTY, ...s, end_date: s.end_date || '', merchant: s.merchant || '', payment_method: s.payment_method || '', payment_ref: s.payment_ref || '', note: s.note || '' })}
                          >
                            <Pencil size={14} />
                          </button>
                          <button className="btn ghost small" title="Delete" onClick={() => remove(s)} style={{ color: 'var(--danger)' }}>
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {!shown.length && (
                <EmptyRow cols={writable ? 9 : 8}>
                  {list.length ? 'Nothing here.' : 'No subscriptions yet. Add one, or use “Find my subscriptions” to pick them up from your bank SMS.'}
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <SubscriptionForm
          initial={editing}
          canLog={hasFeature(user, 'expense')}
          onClose={() => setEditing(null)}
          onSave={async (form) => {
            const body = toPayload(form)
            const ok = await run(
              () => (form.id ? api.put(`/subscriptions/${form.id}`, body) : api.post('/subscriptions', body, { params })),
              form.id ? 'Subscription updated' : `${form.name} added`,
            )
            if (ok) setEditing(null)
          }}
        />
      )}
      {importing && (
        <ImportModal
          params={params}
          onClose={() => setImporting(false)}
          onAdded={(n) => {
            setImporting(false)
            setMsg({ type: 'ok', text: `${n} subscription${n === 1 ? '' : 's'} added` })
            load()
          }}
        />
      )}
      {history && <ChargeHistory sub={history} onClose={() => setHistory(null)} />}
    </div>
  )
}

function SubscriptionForm({ initial, canLog, onClose, onSave }) {
  const [f, setF] = useState(initial)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  return (
    <Modal title={f.id ? `Edit ${initial.name}` : 'Add subscription'} subtitle="Netflix, gym, insurance, phone recharge, rent… anything that repeats." onClose={onClose} wide>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSave(f)
        }}
      >
        <div className="form-grid">
          <div>
            <label>Name</label>
            <input value={f.name} onChange={set('name')} required maxLength={100} placeholder="Netflix Premium" autoFocus />
          </div>
          <div>
            <label>Amount charged</label>
            <input type="number" min="0.01" step="0.01" value={f.amount} onChange={set('amount')} required />
          </div>
          <div>
            <label>How often</label>
            <select value={f.frequency} onChange={set('frequency')}>
              {FREQUENCIES.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          {f.frequency === 'CUSTOM' && (
            <div>
              <label>Every</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input type="number" min="1" max="3650" value={f.interval_count} onChange={set('interval_count')} required style={{ width: 90 }} />
                <select value={f.interval_unit} onChange={set('interval_unit')}>
                  {UNITS.map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
          <div>
            <label>First charge (start)</label>
            <input type="date" value={f.start_date} onChange={set('start_date')} required />
          </div>
          <div>
            <label>Ends on (optional)</label>
            <input type="date" value={f.end_date} min={f.start_date} onChange={set('end_date')} />
          </div>
          <div>
            <label>Paid with</label>
            <select value={f.payment_method} onChange={set('payment_method')}>
              <option value="">Not set</option>
              {Object.entries(PAYMENT_METHODS).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>UPI ID / mandate ref (optional)</label>
            <input value={f.payment_ref} onChange={set('payment_ref')} maxLength={100} placeholder="netflix@ybl" />
          </div>
          <div>
            <label>Merchant (optional)</label>
            <input value={f.merchant} onChange={set('merchant')} maxLength={100} />
          </div>
          <div>
            <label>Category</label>
            <input value={f.category} onChange={set('category')} required maxLength={50} />
          </div>
          <div>
            <label>Remind me</label>
            <select value={f.remind_days_before} onChange={set('remind_days_before')}>
              <option value={0}>No reminder</option>
              {[1, 2, 3, 5, 7, 14].map((d) => (
                <option key={d} value={d}>
                  {d} day{d === 1 ? '' : 's'} before
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Note</label>
            <input value={f.note} onChange={set('note')} maxLength={500} />
          </div>
        </div>
        <div className="checks" style={{ marginTop: 12 }}>
          <label className="check">
            <input type="checkbox" checked={f.auto_log_expense} disabled={!canLog} onChange={set('auto_log_expense')} />
            Add each charge to my expenses automatically
          </label>
          <label className="check">
            <input type="checkbox" checked={f.is_active} onChange={set('is_active')} />
            Active
          </label>
        </div>
        <p className="hint">Charges keep the day of the month of the first charge (a 31st becomes the last day of shorter months). Past charges aren't added.</p>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">{f.id ? 'Save changes' : 'Add subscription'}</button>
        </div>
      </form>
    </Modal>
  )
}

function ImportModal({ params, onClose, onAdded }) {
  const [mode, setMode] = useState('sms')
  const [text, setText] = useState('')
  const [found, setFound] = useState(null)
  const [picked, setPicked] = useState(new Set())
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const show = (list) => {
    setFound(list)
    setPicked(new Set(list.map((s, i) => (s.already_tracked ? null : i)).filter((i) => i !== null)))
    setMsg(list.length ? null : { type: 'info', text: 'No subscriptions found.' })
  }

  const search = async () => {
    setBusy(true)
    try {
      show((mode === 'sms' ? await api.post('/subscriptions/import/sms', { text }, { params }) : await api.get('/subscriptions/import/detect', { params })).data)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    setFound(null)
    setMsg(null)
    if (mode === 'detect') search()
  }, [mode])

  const add = async () => {
    const items = found
      .filter((_, i) => picked.has(i))
      .map(({ already_tracked, confidence, evidence, ...s }) => ({ ...s, remind_days_before: 1, is_active: true, note: null }))
    setBusy(true)
    try {
      await api.post('/subscriptions/bulk', { items }, { params })
      onAdded(items.length)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      setBusy(false)
    }
  }

  return (
    <Modal title="Find my subscriptions" subtitle="Pick up the recurring payments you already have, then choose which to track." onClose={onClose} wide>
      <div className="segmented" style={{ marginBottom: 14 }}>
        <button className={mode === 'sms' ? 'on' : ''} onClick={() => setMode('sms')}>
          From bank / UPI SMS
        </button>
        <button className={mode === 'detect' ? 'on' : ''} onClick={() => setMode('detect')}>
          From my expenses
        </button>
      </div>

      {mode === 'sms' ? (
        <>
          <Alert type="info">
            Google Pay, PhonePe and Paytm don’t let other apps read your AutoPay list. Instead, copy the SMS or email alerts your
            bank or UPI app sends (“AutoPay mandate created…”, “debited towards…”) and paste them below, as many as you like.
            Nothing is saved until you choose.
          </Alert>
          <textarea
            rows={7}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'UPI AutoPay mandate created successfully for NETFLIX of Rs 649.00, frequency Monthly via PhonePe. -HDFC Bank\n\nRs.149.00 debited from A/c XX1234 on 05-10-26 towards UPI Mandate YOUTUBE PREMIUM'}
            style={{ width: '100%', fontFamily: 'inherit' }}
          />
          <div className="form-actions">
            <button className="btn" disabled={busy || text.trim().length < 10} onClick={search}>
              <ScanSearch size={16} /> Find subscriptions
            </button>
          </div>
        </>
      ) : (
        <p className="muted">Items you paid at a regular interval (every week, month, quarter or year) for a similar amount over the past year.</p>
      )}

      <Msg msg={msg} />
      {busy && !found && <p className="muted">Searching…</p>}
      {found?.length > 0 && (
        <>
          <ul className="check-list" style={{ maxHeight: 340, marginTop: 12 }}>
            {found.map((s, i) => (
              <li key={i} className={s.already_tracked ? 'disabled' : ''}>
                <input
                  type="checkbox"
                  checked={picked.has(i)}
                  onChange={() =>
                    setPicked((cur) => {
                      const next = new Set(cur)
                      next.has(i) ? next.delete(i) : next.add(i)
                      return next
                    })
                  }
                />
                <div className="grow" title={s.evidence.join('\n')}>
                  <b>{s.name}</b> {s.already_tracked && <span className="badge">already tracked</span>}{' '}
                  <span className={`badge ${s.confidence === 'high' ? 'ok' : 'warn'}`}>{s.confidence === 'high' ? 'sure' : 'likely'}</span>
                  <div className="hint">
                    {freqLabel(s)} · since {fmtDate(s.start_date)}
                    {s.payment_method && ` · ${PAYMENT_METHODS[s.payment_method]}`}
                    {s.source === 'DETECTED' && ' · not added to expenses again'}
                  </div>
                </div>
                <b>{money(s.amount)}</b>
              </li>
            ))}
          </ul>
          <div className="form-actions">
            <button className="btn" disabled={busy || picked.size === 0} onClick={add}>
              <Plus size={16} /> Add {picked.size} selected
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}

function ChargeHistory({ sub, onClose }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    api.get(`/subscriptions/${sub.id}/charges`).then((r) => setRows(r.data)).catch(() => setRows([]))
  }, [sub.id])
  return (
    <Modal title={`${sub.name}: charges`} subtitle="Recorded on each renewal date while the subscription was active." onClose={onClose}>
      {!rows ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th className="num">Amount</th>
                <th>Expense</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDate(c.charge_date)}</td>
                  <td className="num strong">{money(c.amount)}</td>
                  <td>{c.expense_id ? <span className="badge ok">logged</span> : <span className="muted">not logged</span>}</td>
                </tr>
              ))}
              {!rows.length && <EmptyRow cols={3}>No charges yet. The first one is recorded on {fmtDate(sub.next_charge_date)}.</EmptyRow>}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  )
}
