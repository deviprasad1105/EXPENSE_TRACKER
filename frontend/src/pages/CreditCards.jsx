import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AlarmOutlined from '@mui/icons-material/AlarmOutlined'
import AccountBalanceWalletOutlined from '@mui/icons-material/AccountBalanceWalletOutlined'
import CreditCardOutlined from '@mui/icons-material/CreditCardOutlined'
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded'
import api, { errorMessage, money, niceDate, todayISO } from '../api'
import { useAuth } from '../auth'
import { Msg, PageHeader, StatTile } from '../components/ui'

const SOURCE = { SMS: 'from the statement SMS', MANUAL: 'typed in', ESTIMATE: 'estimated from what the card owes' }

function dueText(bill) {
  if (bill.status === 'PAID') return ['ok', 'Paid']
  const d = bill.days_left
  if (d < 0) return ['bad', `Overdue by ${-d} day${d === -1 ? '' : 's'}`]
  if (d === 0) return ['bad', 'Due today']
  if (d <= 3) return ['warn', `Due in ${d} day${d === 1 ? '' : 's'}`]
  return ['info', `Due in ${d} days`]
}

export default function CreditCards() {
  const { params, target } = useAuth()
  const canWrite = target?.writable !== false
  const [cards, setCards] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [open, setOpen] = useState({})
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [c, a] = await Promise.all([api.get('/cards', { params }), api.get('/accounts', { params })])
    setCards(c.data)
    setAccounts(a.data.filter((x) => x.is_active && x.kind !== 'CREDIT_CARD'))
  }, [params])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const run = async (fn, ok) => {
    setMsg(null)
    try {
      await fn()
      await load()
      if (ok) setMsg({ type: 'ok', text: ok })
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }

  const active = (cards || []).filter((c) => c.is_active)
  const unpaid = active.filter((c) => c.bill && !c.bill.paid)
  const owed = active.reduce((t, c) => t + Number(c.owed), 0)
  const soon = unpaid.filter((c) => c.bill.days_left >= 0 && c.bill.days_left <= 7)
  const overdue = unpaid.filter((c) => c.bill.days_left < 0)
  const limits = active.filter((c) => c.available != null)

  return (
    <div>
      <PageHeader title="Credit Cards" subtitle="What each card owes, when its bill is due, and reminders before the due date." />
      <Msg msg={msg} />

      <div className="tiles">
        <StatTile hero label="Total owed on cards" value={money(owed)} sub={`${active.length} card(s)`} icon={CreditCardOutlined} />
        <StatTile label="Due in the next 7 days" value={money(soon.reduce((t, c) => t + Number(c.bill.total_due) - Number(c.bill.paid_amount), 0))} sub={`${soon.length} bill(s)`} icon={AlarmOutlined} />
        <StatTile label="Overdue" value={overdue.length} sub={overdue.length ? overdue.map((c) => c.name).join(', ') : 'Nothing overdue'} icon={WarningAmberRounded} negative={overdue.length > 0} />
        <StatTile label="Credit available" value={limits.length ? money(limits.reduce((t, c) => t + Number(c.available), 0)) : '—'} sub={limits.length ? `on ${limits.length} card(s) with a limit set` : 'Set each card’s limit below'} icon={AccountBalanceWalletOutlined} />
      </div>

      {cards && !cards.length && (
        <div className="card">
          <h3>No credit cards yet</h3>
          <p className="muted">
            Add your cards in <Link to="/settings#accounts">Settings → Bank accounts</Link> with the type <b>Credit card</b> (and their last 4 digits, so auto-tracking finds them).
          </p>
        </div>
      )}

      <div className="cc-grid">
        {(cards || []).map((c) => (
          <CardPanel key={c.id} c={c} accounts={accounts} panel={open[c.id]} setPanel={(p) => setOpen({ ...open, [c.id]: open[c.id] === p ? null : p })} run={run} canWrite={canWrite} params={params} />
        ))}
      </div>
    </div>
  )
}

function CardPanel({ c, accounts, panel, setPanel, run, canWrite, params }) {
  const bill = c.bill
  const [tone, label] = bill ? dueText(bill) : ['', '']
  const left = bill ? Number(bill.total_due) - Number(bill.paid_amount) : 0
  const used = c.used_percent ?? null
  return (
    <div className={`card cc-card ${c.is_active ? '' : 'muted'}`}>
      <div className="row-between">
        <div>
          <h3 style={{ margin: 0 }}>
            {c.name}
            {c.last4 ? <span className="muted"> ••{c.last4}</span> : null}
          </h3>
          {c.bank && <div className="hint">{c.bank}</div>}
        </div>
        {bill && <span className={`badge ${tone}`}>{label}</span>}
        {!c.is_active && <span className="badge">Inactive</span>}
      </div>

      <div className="cc-owed">
        <div>
          <div className="hint">Owes now</div>
          <div className="cc-amount">{money(c.owed)}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="hint">Spent this month</div>
          <div className="strong">{money(c.spent_this_month)}</div>
        </div>
      </div>
      {c.credit_limit != null && (
        <div>
          <div className="progress" title={`${used}% of the limit used`}>
            <i style={{ width: `${Math.min(100, used || 0)}%`, background: used > 80 ? 'var(--danger)' : used > 50 ? 'var(--warning)' : 'var(--primary)' }} />
          </div>
          <div className="hint">
            {money(c.available)} available of {money(c.credit_limit)} ({used}% used)
          </div>
        </div>
      )}

      {bill ? (
        <div className="cc-bill">
          <div className="row-between">
            <div>
              <div className="hint">Bill due {niceDate(bill.due_date, { weekday: true })}</div>
              <div className="strong">
                {money(bill.total_due)}
                {bill.min_due ? <span className="muted"> · minimum {money(bill.min_due)}</span> : null}
              </div>
            </div>
            {!bill.paid && Number(bill.paid_amount) > 0 && <div className="hint">{money(bill.paid_amount)} paid, {money(left)} left</div>}
          </div>
          <div className="hint">
            {SOURCE[bill.source]}
            {bill.statement_date ? ` · statement ${niceDate(bill.statement_date)}` : ''}
          </div>
        </div>
      ) : (
        <p className="hint">{c.due_day ? 'Nothing owed, so no bill.' : 'Set the due day (or add the bill) to see when to pay and get reminders.'}</p>
      )}
      {c.is_active && (
        <p className="hint" style={{ margin: 0 }}>
          {c.remind_on ? `Reminder ${c.remind_days ? `${c.remind_days} day(s) before, ` : ''}on the due day and if overdue` : 'Reminders off'}
          {c.due_day ? ` · due on day ${c.due_day} each month` : ''}
        </p>
      )}

      {canWrite && c.is_active && (
        <div className="cc-actions">
          {bill && !bill.paid && (
            <button className="btn small" onClick={() => setPanel('pay')}>
              Pay
            </button>
          )}
          <button className="btn secondary small" onClick={() => setPanel('bill')}>
            Add bill
          </button>
          {bill && (
            <button className="btn ghost small" onClick={() => run(() => api.post(`/cards/bills/${bill.id}/paid`, { paid: !bill.paid }), bill.paid ? 'Marked as not paid.' : 'Marked as paid.')}>
              {bill.paid ? 'Mark not paid' : 'Mark paid'}
            </button>
          )}
          <button className="btn ghost small" onClick={() => setPanel('settings')}>
            Reminders & limit
          </button>
          <button className="btn ghost small" onClick={() => setPanel('history')}>
            History
          </button>
        </div>
      )}
      {panel === 'pay' && <PayForm c={c} left={left} accounts={accounts} run={run} params={params} close={() => setPanel(null)} />}
      {panel === 'bill' && <BillForm c={c} run={run} close={() => setPanel(null)} />}
      {panel === 'settings' && <SettingsForm c={c} run={run} close={() => setPanel(null)} />}
      {panel === 'history' && <History c={c} run={run} canWrite={canWrite} />}
    </div>
  )
}

function PayForm({ c, left, accounts, run, params, close }) {
  const [f, setF] = useState({ from: accounts[0]?.id || '', amount: left > 0 ? left.toFixed(2) : '', date: todayISO() })
  const submit = async (e) => {
    e.preventDefault()
    const ok = await run(
      () => api.post('/accounts/transfers', { from_account_id: Number(f.from), to_account_id: c.id, amount: f.amount, transfer_date: f.date, note: `${c.name} bill payment` }, { params }),
      `Payment of ${money(f.amount)} to ${c.name} recorded.`,
    )
    if (ok) close()
  }
  return (
    <form className="cc-form" onSubmit={submit}>
      <div className="hint">Records the payment as a transfer from your bank to the card (the bill counts as paid once payments cover it).</div>
      <div className="form-grid">
        <div>
          <label>From</label>
          <select value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} required>
            <option value="">Choose…</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>Amount</label>
          <input type="number" min="0.01" step="0.01" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required />
        </div>
        <div>
          <label>Date</label>
          <input type="date" max={todayISO()} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} required />
        </div>
        <div className="form-actions">
          <button className="btn">Record payment</button>
        </div>
      </div>
    </form>
  )
}

function BillForm({ c, run, close }) {
  const [f, setF] = useState({ due_date: c.bill?.due_date || '', total_due: '', min_due: '', statement_date: '' })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    const ok = await run(
      () => api.post(`/cards/${c.id}/bills`, { ...f, min_due: f.min_due || null, statement_date: f.statement_date || null }),
      `Bill for ${c.name} saved.`,
    )
    if (ok) close()
  }
  return (
    <form className="cc-form" onSubmit={submit}>
      <div className="hint">From the card’s statement. It replaces an estimated or SMS bill for the same month.</div>
      <div className="form-grid">
        <div>
          <label>Due date</label>
          <input type="date" value={f.due_date} onChange={set('due_date')} required />
        </div>
        <div>
          <label>Total due</label>
          <input type="number" min="0.01" step="0.01" value={f.total_due} onChange={set('total_due')} required />
        </div>
        <div>
          <label>Minimum due</label>
          <input type="number" min="0" step="0.01" value={f.min_due} onChange={set('min_due')} />
        </div>
        <div>
          <label>Statement date</label>
          <input type="date" value={f.statement_date} max={f.due_date || undefined} onChange={set('statement_date')} />
        </div>
        <div className="form-actions">
          <button className="btn">Save bill</button>
        </div>
      </div>
    </form>
  )
}

function SettingsForm({ c, run, close }) {
  const [f, setF] = useState({
    credit_limit: c.credit_limit ?? '',
    statement_day: c.statement_day ?? '',
    due_day: c.due_day ?? '',
    remind_days: c.remind_days,
    remind_on: c.remind_on,
  })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    const n = (v) => (v === '' || v == null ? null : Number(v))
    const ok = await run(
      () => api.put(`/cards/${c.id}/settings`, { credit_limit: f.credit_limit === '' ? null : f.credit_limit, statement_day: n(f.statement_day), due_day: n(f.due_day), remind_days: Number(f.remind_days), remind_on: f.remind_on }),
      `${c.name} settings saved.`,
    )
    if (ok) close()
  }
  return (
    <form className="cc-form" onSubmit={submit}>
      <div className="form-grid">
        <div>
          <label>Credit limit</label>
          <input type="number" min="0" step="0.01" value={f.credit_limit} onChange={set('credit_limit')} placeholder="e.g. 100000" />
        </div>
        <div>
          <label>Statement day</label>
          <input type="number" min="1" max="31" value={f.statement_day} onChange={set('statement_day')} placeholder="1–31" />
        </div>
        <div>
          <label>Due day</label>
          <input type="number" min="1" max="31" value={f.due_day} onChange={set('due_day')} placeholder="1–31" />
          <span className="hint">The day of the month the bill is due</span>
        </div>
        <div>
          <label>Remind me (days before)</label>
          <input type="number" min="0" max="15" value={f.remind_days} onChange={set('remind_days')} required />
        </div>
        <div>
          <label className="row-between" style={{ justifyContent: 'flex-start' }}>
            <span className="switch">
              <input type="checkbox" checked={f.remind_on} onChange={(e) => setF({ ...f, remind_on: e.target.checked })} />
              <span />
            </span>
            Reminders (notification and email)
          </label>
        </div>
        <div className="form-actions">
          <button className="btn">Save</button>
        </div>
      </div>
    </form>
  )
}

function History({ c, run, canWrite }) {
  const [bills, setBills] = useState(null)
  const load = useCallback(() => api.get(`/cards/${c.id}/bills`).then((r) => setBills(r.data)), [c.id])
  useEffect(() => {
    load().catch(() => setBills([]))
  }, [load, c.bill?.id, c.bill?.status])
  if (!bills) return <p className="hint">Loading…</p>
  if (!bills.length) return <p className="hint">No bills yet.</p>
  return (
    <div className="table-wrap cc-form">
      <table className="table mini-table">
        <thead>
          <tr>
            <th>Due</th>
            <th className="num">Bill</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {bills.map((b) => {
            const [tone, label] = dueText(b)
            return (
              <tr key={b.id}>
                <td>{niceDate(b.due_date)}</td>
                <td className="num">{money(b.total_due)}</td>
                <td>
                  <span className={`badge ${tone}`}>{label}</span>
                </td>
                <td className="num">
                  {canWrite && (
                    <button className="btn ghost small" onClick={() => window.confirm('Delete this bill?') && run(() => api.delete(`/cards/bills/${b.id}`).then(load))}>
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
