import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlarmClock, CalendarClock, CheckCircle2, History, Pencil, Plus, ReceiptText, SkipForward, Trash2, Undo2 } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { hasFeature, useAuth } from '../auth'
import Modal from './Modal'
import DateInput from './DateInput'
import { EmptyRow, Msg, StatTile } from './ui'
import { FREQUENCIES, UNITS, dueLabel, fmtDate, freqLabel } from '../repeatSchedule'

export const BILL_KINDS = {
  ELECTRICITY: 'Electricity',
  MOBILE: 'Mobile',
  BROADBAND: 'Broadband / Wi-Fi',
  DTH: 'DTH / cable TV',
  GAS: 'Gas',
  WATER: 'Water',
  RENT: 'Rent',
  INSURANCE: 'Insurance premium',
  EMI: 'Loan EMI',
  SCHOOL: 'School / tuition fees',
  TAX: 'Property / other tax',
  MAINTENANCE: 'Society maintenance',
  OTHER: 'Other',
}

const STATUS = {
  OVERDUE: ['Overdue', 'bad'],
  DUE_SOON: ['Due soon', 'warn'],
  UPCOMING: ['Upcoming', ''],
  PAUSED: ['Paused', ''],
  DONE: ['Finished', ''],
}

const EMPTY = {
  name: '',
  biller: '',
  kind: 'ELECTRICITY',
  category: 'Bills',
  amount: '',
  frequency: 'MONTHLY',
  interval_count: 1,
  interval_unit: 'MONTH',
  due_date: todayISO(),
  end_date: '',
  account_id: '',
  remind_days_before: 3,
  is_active: true,
  note: '',
}

const toPayload = (f) => ({
  ...f,
  amount: f.amount === '' || f.amount === null ? null : Number(f.amount),
  interval_count: Number(f.interval_count) || 1,
  remind_days_before: Number(f.remind_days_before) || 0,
  biller: f.biller || null,
  end_date: f.end_date || null,
  account_id: f.account_id ? Number(f.account_id) : null,
  note: f.note || null,
})

const dueText = (b) => (b.days_until < 0 ? `${-b.days_until} day${b.days_until === -1 ? '' : 's'} late` : dueLabel(b.days_until))

/** Bills & Subscriptions → Bills: bills paid by hand (electricity, mobile, rent, EMIs…), with reminders. */
export default function Bills({ header }) {
  const { params, target, user } = useAuth()
  const [list, setList] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [editing, setEditing] = useState(null)
  const [paying, setPaying] = useState(null)
  const [history, setHistory] = useState(null)
  const [msg, setMsg] = useState(null)
  const writable = target?.writable
  const canLog = hasFeature(user, 'expense')

  const load = useCallback(async () => {
    setList((await api.get('/bills', { params })).data)
  }, [params])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
    // Accounts are part of expense tracking: without it, bills are just not tied to an account
    if (canLog) api.get('/accounts', { params }).then((r) => setAccounts(r.data.filter((a) => a.is_active))).catch(() => {})
  }, [load, canLog, params])

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

  const next = (res) =>
    res.data.next_due_date ? ` Next one: ${fmtDate(res.data.next_due_date)}.` : ' That was the last one.'
  const confirmMatch = (b) =>
    run(() => api.post(`/bills/${b.id}/pay`, { expense_id: b.match.expense_id }), (res) => `${b.name} marked paid with ${money(b.match.amount)} on ${fmtDate(b.match.expense_date)}.${next(res)}`)
  const skip = (b) =>
    window.confirm(`Skip the ${b.name} bill due ${fmtDate(b.next_due_date)}? Use this when there's nothing to pay this time.`) &&
    run(() => api.post(`/bills/${b.id}/skip`), (res) => `${b.name}: skipped.${next(res)}`)
  const remove = (b) =>
    window.confirm(`Delete "${b.name}"? Payments already in your expenses stay there.`) &&
    run(() => api.delete(`/bills/${b.id}`), user.role === 'ADMIN' ? 'Bill deleted. A backup was saved (Administration → Backups).' : 'Bill deleted')

  const active = useMemo(() => (list || []).filter((b) => b.is_active && b.next_due_date), [list])
  const overdue = active.filter((b) => b.status === 'OVERDUE')
  const week = active.filter((b) => b.days_until >= 0 && b.days_until <= 7)
  const sum = (bills) => bills.reduce((t, b) => t + Number(b.amount || b.last_amount || 0), 0)

  return (
    <div>
      {header(
        writable && (
          <button className="btn" onClick={() => setEditing({ ...EMPTY })}>
            <Plus size={16} /> Add bill
          </button>
        ),
      )}
      <Msg msg={msg} />

      {list && (
        <div className="tiles">
          <StatTile hero icon={ReceiptText} label="Bills" value={active.length} sub={`${list.length - active.length} paused or finished`} />
          <StatTile icon={CalendarClock} label="Due in the next 7 days" value={money(sum(week))} sub={`${week.length} bill${week.length === 1 ? '' : 's'} · usual amounts`} />
          <StatTile icon={AlarmClock} label="Overdue" value={overdue.length} negative={overdue.length > 0} sub={!overdue.length ? 'All caught up' : sum(overdue) ? money(sum(overdue)) : 'amount varies'} />
        </div>
      )}

      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Bill</th>
                <th className="num">Amount</th>
                <th>How often</th>
                <th>Due</th>
                <th>Last paid</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(list || []).map((b) => {
                const [label, cls] = STATUS[b.status]
                const open = b.is_active && b.next_due_date
                return (
                  <tr key={b.id} style={open ? null : { opacity: 0.65 }}>
                    <td>
                      <b>{b.name}</b>
                      <div className="hint">
                        {BILL_KINDS[b.kind]}
                        {b.biller && b.biller !== b.name ? ` · ${b.biller}` : ''}
                      </div>
                    </td>
                    <td className="num strong">{b.amount ? money(b.amount) : <span className="muted" title="Changes every time">varies</span>}</td>
                    <td>
                      {freqLabel(b)}
                      {b.end_date && <div className="hint">last on {fmtDate(b.end_date)}</div>}
                    </td>
                    <td>
                      {open ? (
                        <>
                          {fmtDate(b.next_due_date)} <span className={`badge ${cls}`}>{b.status === 'UPCOMING' ? dueText(b) : label}</span>
                          <div className="hint">
                            {b.status !== 'UPCOMING' && `${dueText(b)} · `}
                            {b.remind_days_before > 0 ? `reminder ${b.remind_days_before} day${b.remind_days_before === 1 ? '' : 's'} before` : 'no reminder'}
                          </div>
                          {b.match && (
                            <div className="bill-match">
                              Looks paid: {money(b.match.amount)} on {fmtDate(b.match.expense_date)} ({b.match.item_name})
                              {writable && (
                                <button className="link-button" onClick={() => confirmMatch(b)}>
                                  Confirm
                                </button>
                              )}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className={`badge ${cls}`}>{label}</span>
                      )}
                    </td>
                    <td>
                      {b.last_paid_on ? (
                        <>
                          {fmtDate(b.last_paid_on)}
                          <div className="hint">{money(b.last_amount)}</div>
                        </>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <div className="actions bill-actions">
                        {writable && open && (
                          <>
                            <button className="btn small" onClick={() => setPaying(b)}>
                              <CheckCircle2 size={14} /> Mark paid
                            </button>
                            <button className="btn ghost small" title="Skip this one (nothing to pay)" onClick={() => skip(b)}>
                              <SkipForward size={14} />
                            </button>
                          </>
                        )}
                        <button className="btn ghost small" title="Payment history" onClick={() => setHistory(b)}>
                          <History size={14} />
                        </button>
                        {writable && (
                          <>
                            <button
                              className="btn ghost small"
                              title="Edit"
                              onClick={() =>
                                setEditing({
                                  ...EMPTY,
                                  ...b,
                                  amount: b.amount ?? '',
                                  biller: b.biller || '',
                                  due_date: b.next_due_date || todayISO(),
                                  end_date: b.end_date || '',
                                  account_id: b.account_id || '',
                                  note: b.note || '',
                                })
                              }
                            >
                              <Pencil size={14} />
                            </button>
                            <button className="btn ghost small" title="Delete" onClick={() => remove(b)} style={{ color: 'var(--danger)' }}>
                              <Trash2 size={14} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
              {list && !list.length && (
                <EmptyRow cols={6}>No bills yet. Add electricity, mobile, rent, insurance or an EMI to get a reminder before each due date.</EmptyRow>
              )}
              {!list && <EmptyRow cols={6}>Loading…</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <BillForm
          initial={editing}
          accounts={accounts}
          onClose={() => setEditing(null)}
          onSave={async (form) => {
            const body = toPayload(form)
            const ok = await run(
              () => (form.id ? api.put(`/bills/${form.id}`, body) : api.post('/bills', body, { params })),
              form.id ? 'Bill updated' : `${form.name} added. You'll get a reminder before it's due.`,
            )
            if (ok) setEditing(null)
          }}
        />
      )}
      {paying && (
        <PayBill
          bill={paying}
          accounts={accounts}
          canLog={canLog}
          onClose={() => setPaying(null)}
          onPay={async (body) => {
            const ok = await run(() => api.post(`/bills/${paying.id}/pay`, body), (res) => `${paying.name} marked paid.${next(res)}`)
            if (ok) setPaying(null)
          }}
        />
      )}
      {history && (
        <PaymentHistory
          bill={history}
          writable={writable}
          onClose={() => setHistory(null)}
          onUndo={(p) =>
            window.confirm(
              `Undo this ${p.amount ? 'payment' : 'skip'}? The bill due ${fmtDate(p.due_date)} will be due again.${p.made_expense ? ' The expense it added is removed.' : ''}`,
            ) &&
            run(() => api.delete(`/bills/${history.id}/payments/${p.id}`), 'Undone: the bill is due again').then((ok) => ok && setHistory(null))
          }
        />
      )}
    </div>
  )
}

function AccountSelect({ accounts, value, onChange, empty }) {
  return (
    <select value={value} onChange={onChange}>
      <option value="">{empty}</option>
      {accounts.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  )
}

function BillForm({ initial, accounts, onClose, onSave }) {
  const [f, setF] = useState(initial)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  return (
    <Modal title={f.id ? `Edit ${initial.name}` : 'Add bill'} subtitle="A bill you pay yourself when it comes: electricity, mobile, rent, insurance, EMIs…" onClose={onClose} wide>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSave(f)
        }}
      >
        <div className="form-grid">
          <div>
            <label>Type</label>
            <select
              value={f.kind}
              onChange={(e) => setF({ ...f, kind: e.target.value, name: f.name || (e.target.value === 'OTHER' ? '' : BILL_KINDS[e.target.value]) })}
            >
              {Object.entries(BILL_KINDS).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Name</label>
            <input value={f.name} onChange={set('name')} required maxLength={100} placeholder="Electricity" autoFocus />
          </div>
          <div>
            <label>Paid to (optional)</label>
            <input value={f.biller} onChange={set('biller')} maxLength={100} placeholder="MSEDCL, Airtel, landlord…" />
            <span className="hint">Helps spot the payment among your expenses</span>
          </div>
          <div>
            <label>Usual amount (optional)</label>
            <input type="number" min="0.01" step="0.01" value={f.amount} onChange={set('amount')} placeholder="Leave empty if it changes" />
          </div>
          <div>
            <label>Next due date</label>
            <DateInput value={f.due_date} onChange={set('due_date')} required />
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
            <label>Last bill on (optional)</label>
            <DateInput value={f.end_date} min={f.due_date} onChange={set('end_date')} />
            <span className="hint">The last EMI, or the end of a lease</span>
          </div>
          <div>
            <label>Remind me</label>
            <select value={f.remind_days_before} onChange={set('remind_days_before')}>
              <option value={0}>No reminder</option>
              {[1, 2, 3, 5, 7, 10, 15].map((d) => (
                <option key={d} value={d}>
                  {d} day{d === 1 ? '' : 's'} before
                </option>
              ))}
            </select>
          </div>
          {accounts.length > 0 && (
            <div>
              <label>Usually paid from</label>
              <AccountSelect accounts={accounts} value={f.account_id} onChange={set('account_id')} empty="Main account" />
            </div>
          )}
          <div>
            <label>Category</label>
            <input value={f.category} onChange={set('category')} required maxLength={50} />
          </div>
          <div>
            <label>Note</label>
            <input value={f.note} onChange={set('note')} maxLength={500} placeholder="Consumer no., policy no.…" />
          </div>
        </div>
        {f.id && (
          <div className="checks" style={{ marginTop: 12 }}>
            <label className="check">
              <input type="checkbox" checked={f.is_active} onChange={set('is_active')} />
              Active (paused bills get no reminders)
            </label>
          </div>
        )}
        <p className="hint">Later bills fall on the same day of the month (a 31st becomes the last day of shorter months). You also get a reminder the day after a bill is overdue.</p>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">{f.id ? 'Save changes' : 'Add bill'}</button>
        </div>
      </form>
    </Modal>
  )
}

function PayBill({ bill, accounts, canLog, onClose, onPay }) {
  const [matches, setMatches] = useState(null)
  const [pick, setPick] = useState('new')
  const [f, setF] = useState({ amount: bill.amount ?? '', paid_on: todayISO(), log_expense: canLog, account_id: bill.account_id || '' })
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  useEffect(() => {
    api
      .get(`/bills/${bill.id}/matches`)
      .then((r) => {
        setMatches(r.data)
        if (r.data.length) setPick(String(r.data[0].expense_id))
      })
      .catch(() => setMatches([]))
  }, [bill.id])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    await onPay(
      pick === 'new'
        ? { amount: Number(f.amount), paid_on: f.paid_on, log_expense: f.log_expense, account_id: f.account_id ? Number(f.account_id) : null }
        : { expense_id: Number(pick) },
    )
    setBusy(false)
  }

  return (
    <Modal title={`Mark ${bill.name} paid`} subtitle={`The bill due ${fmtDate(bill.next_due_date)}`} onClose={onClose}>
      <form onSubmit={submit}>
        {matches === null ? (
          <p className="muted">Looking for the payment in your expenses…</p>
        ) : matches.length > 0 && (
          <ul className="check-list" style={{ maxHeight: 'none' }}>
            {matches.map((m) => (
              <li key={m.expense_id}>
                <input type="radio" name="pay" checked={pick === String(m.expense_id)} onChange={() => setPick(String(m.expense_id))} />
                <span className="grow">
                  <b>Already recorded:</b> {m.item_name}
                  <div className="hint">{fmtDate(m.expense_date)} · not added again</div>
                </span>
                <b>{money(m.amount)}</b>
              </li>
            ))}
            <li>
              <input type="radio" name="pay" checked={pick === 'new'} onChange={() => setPick('new')} />
              <span className="grow">
                <b>None of these: enter the payment</b>
              </span>
            </li>
          </ul>
        )}
        {pick === 'new' && (
          <div className="form-grid" style={{ marginTop: 12 }}>
            <div>
              <label>Amount paid</label>
              <input type="number" min="0.01" step="0.01" value={f.amount} onChange={set('amount')} required autoFocus />
            </div>
            <div>
              <label>Paid on</label>
              <DateInput value={f.paid_on} onChange={set('paid_on')} required />
            </div>
            {canLog && f.log_expense && accounts.length > 0 && (
              <div>
                <label>Paid from</label>
                <AccountSelect accounts={accounts} value={f.account_id} onChange={set('account_id')} empty="Main account" />
              </div>
            )}
          </div>
        )}
        {pick === 'new' && canLog && (
          <div className="checks" style={{ marginTop: 12 }}>
            <label className="check">
              <input type="checkbox" checked={f.log_expense} onChange={set('log_expense')} />
              Add it to my expenses
            </label>
          </div>
        )}
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" disabled={busy || matches === null}>
            <CheckCircle2 size={16} /> {busy ? 'Saving…' : 'Mark paid'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function PaymentHistory({ bill, writable, onClose, onUndo }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    api.get(`/bills/${bill.id}/payments`).then((r) => setRows(r.data)).catch(() => setRows([]))
  }, [bill.id])
  return (
    <Modal title={`${bill.name}: payments`} subtitle="Every bill marked paid or skipped, newest first." onClose={onClose}>
      {!rows ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Due</th>
                <th>Paid on</th>
                <th className="num">Amount</th>
                <th>Expense</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p, i) => (
                <tr key={p.id}>
                  <td>{fmtDate(p.due_date)}</td>
                  <td>{p.amount ? fmtDate(p.paid_on) : <span className="badge">skipped</span>}</td>
                  <td className="num strong">{p.amount ? money(p.amount) : '—'}</td>
                  <td>{p.expense_id ? <span className="badge ok">{p.made_expense ? 'added' : 'linked'}</span> : <span className="muted">—</span>}</td>
                  <td>
                    {writable && i === 0 && (
                      <button className="btn ghost small" title="Undo" onClick={() => onUndo(p)}>
                        <Undo2 size={14} /> Undo
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!rows.length && <EmptyRow cols={5}>Nothing paid yet.</EmptyRow>}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  )
}
