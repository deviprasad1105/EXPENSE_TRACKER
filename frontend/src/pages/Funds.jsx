import { useCallback, useEffect, useState } from 'react'
import { ArrowDownCircle, ArrowUpCircle, Plus, Trash2, Wallet } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { AccountSelect, accountLabel, useAccounts } from '../components/Accounts'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader, StatTile } from '../components/ui'

export default function Funds() {
  const { params, target, user } = useAuth()
  const [list, setList] = useState([])
  const [bal, setBal] = useState(null)
  const [form, setForm] = useState({ fund_date: todayISO(), amount: '', source: 'Salary', note: '' })
  const [msg, setMsg] = useState(null)
  const [accountId, setAccountId] = useState(undefined)
  const [version, setVersion] = useState(0)
  const accounts = useAccounts(version, true)
  const open = accounts.filter((a) => a.is_active)
  const byId = Object.fromEntries(accounts.map((a) => [a.id, a]))

  const load = useCallback(async () => {
    const [f, d] = await Promise.all([api.get('/funds', { params }), api.get('/dashboard', { params })])
    setList(f.data)
    setBal(d.data)
  }, [params])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const submit = async (e) => {
    e.preventDefault()
    try {
      await api.post('/funds', { ...form, amount: Number(form.amount), note: form.note || null, account_id: accountId ?? null }, { params })
      setVersion((v) => v + 1)
      setForm({ ...form, amount: '', note: '' })
      setMsg({ type: 'ok', text: 'Funds added' })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const remove = async (f) => {
    if (!window.confirm(`Delete the fund entry of ${money(f.amount)}?`)) return
    try {
      await api.delete(`/funds/${f.id}`)
      if (user.role === 'ADMIN') setMsg({ type: 'ok', text: 'Fund entry deleted. A backup was saved (Administration → Backups).' })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  return (
    <div>
      <PageHeader title="Balance" subtitle="Add the money you receive. Your available balance is everything added minus everything spent." />
      <ReadOnlyNotice />
      <Msg msg={msg} />
      {bal && (
        <div className="tiles">
          <StatTile hero negative={bal.balance < 0} icon={Wallet} label="Available balance" value={money(bal.balance)} />
          <StatTile icon={ArrowDownCircle} label="Total added" value={money(bal.total_funds)} />
          <StatTile icon={ArrowUpCircle} label="Total spent" value={money(bal.total_expenses)} />
        </div>
      )}
      {target?.writable && (
        <form className="card" onSubmit={submit}>
          <div className="card-head">
            <h3>Add money</h3>
          </div>
          <div className="form-grid">
            <div>
              <label>Date</label>
              <input type="date" value={form.fund_date} onChange={set('fund_date')} required />
            </div>
            <div>
              <label>Amount</label>
              <input type="number" min="0.01" step="0.01" value={form.amount} onChange={set('amount')} required />
            </div>
            <div>
              <label>Source</label>
              <input value={form.source} onChange={set('source')} required />
            </div>
            <div>
              <label>Note</label>
              <input value={form.note} onChange={set('note')} />
            </div>
            <AccountSelect accounts={open} value={accountId} onChange={setAccountId} label="Received in" />
          </div>
          <div className="form-actions">
            <button className="btn">
              <Plus size={16} /> Add funds
            </button>
          </div>
        </form>
      )}
      <div className="card flush">
        <div className="card-head">
          <h3>History</h3>
        </div>
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Source</th>
                <th>Account</th>
                <th className="num">Amount</th>
                <th>Note</th>
                {target?.writable && <th></th>}
              </tr>
            </thead>
            <tbody>
              {list.map((f) => (
                <tr key={f.id}>
                  <td>{f.fund_date}</td>
                  <td className="strong" title={f.raw_name && f.raw_name !== f.source ? `Original: ${f.raw_name}` : undefined}>
                    {f.source} {f.origin === 'UPI' && <span className="badge info">UPI</span>}
                    {f.alias_id && <span className="badge">alias</span>}
                  </td>
                  <td className="muted">{f.account_id && byId[f.account_id] ? accountLabel(byId[f.account_id]) : '—'}</td>
                  <td className="num strong" style={{ color: 'var(--success)' }}>+{money(f.amount)}</td>
                  <td className="muted">{f.note}</td>
                  {target?.writable && (
                    <td>
                      <div className="actions">
                        <button className="btn ghost small" title="Delete" onClick={() => remove(f)} style={{ color: 'var(--danger)' }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
              {!list.length && <EmptyRow cols={6}>No funds yet. Add your salary or pocket money to track your balance.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
