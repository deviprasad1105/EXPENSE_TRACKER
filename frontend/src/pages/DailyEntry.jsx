import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Lock, Plus, X } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { AccountSelect, accountLabel, useAccounts } from '../components/Accounts'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'

const emptyExtra = () => ({ item_name: '', category: 'General', quantity: 1, amount: '', note: '' })

export default function DailyEntry() {
  const { params, target } = useAuth()
  const [day, setDay] = useState(todayISO())
  const [rows, setRows] = useState([])
  const [extras, setExtras] = useState([])
  const [saved, setSaved] = useState([])
  const [categories, setCategories] = useState([])
  const [msg, setMsg] = useState(null)
  const [accountId, setAccountId] = useState(undefined)
  const [accountsVersion, setAccountsVersion] = useState(0)
  const accounts = useAccounts(accountsVersion)

  const load = useCallback(async () => {
    const [items, existing, cats] = await Promise.all([
      api.get('/daily-items', { params: { ...params, active_only: true } }),
      api.get('/expenses', { params: { ...params, start: day, end: day } }),
      api.get('/expenses/categories', { params }),
    ])
    const savedItemIds = new Set(existing.data.map((e) => e.daily_item_id).filter(Boolean))
    setRows(
      items.data.map((it) => ({
        ...it,
        include: !savedItemIds.has(it.id),
        quantity: it.default_quantity,
        unit: Number(it.default_amount),
        amount: Number(it.default_amount) * it.default_quantity,
        alreadySaved: savedItemIds.has(it.id),
      })),
    )
    setSaved(existing.data)
    setCategories(cats.data)
    setExtras([])
  }, [params, day])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const updateRow = (i, patch) =>
    setRows((rs) =>
      rs.map((r, idx) => {
        if (idx !== i) return r
        const next = { ...r, ...patch }
        // Constant items: amount always follows the fixed unit price × quantity
        if (next.is_constant) next.amount = next.unit * Number(next.quantity || 0)
        return next
      }),
    )
  const updateExtra = (i, patch) => setExtras((xs) => xs.map((x, idx) => (idx === i ? { ...x, ...patch } : x)))

  const selected = rows.filter((r) => r.include && Number(r.amount) > 0)
  const validExtras = extras.filter((x) => x.item_name.trim() && Number(x.amount) > 0)
  const total = [...selected, ...validExtras].reduce((s, r) => s + Number(r.amount), 0)

  const save = async () => {
    const items = [
      ...selected.map((r) => ({
        expense_date: day,
        item_name: r.name,
        category: r.category,
        quantity: Number(r.quantity),
        amount: Number(r.amount),
        daily_item_id: r.id,
        is_extra: false,
      })),
      ...validExtras.map((x) => ({
        expense_date: day,
        item_name: x.item_name.trim(),
        category: x.category,
        quantity: Number(x.quantity),
        amount: Number(x.amount),
        note: x.note || null,
        is_extra: true,
      })),
    ]
    if (!items.length) return setMsg({ type: 'error', text: 'Nothing to save. Select at least one item.' })
    const from = accounts.find((a) => a.id === accountId)
    if (!window.confirm(`Save ${items.length} item(s) totalling ${money(total)} for ${day}${from ? `, paid from ${accountLabel(from)}` : ''}?\n\nYou can edit them for the next 3 days. After that they are locked.`))
      return
    try {
      await api.post('/expenses/daily', { expense_date: day, items, account_id: accountId ?? null }, { params })
      setAccountsVersion((v) => v + 1)
      setMsg({ type: 'ok', text: `Saved ${items.length} item(s) for ${day}.` })
      await load()
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    }
  }

  const ro = !target?.writable
  const savedTotal = saved.reduce((s, e) => s + Number(e.amount), 0)

  return (
    <div>
      <PageHeader title="Daily Entry" subtitle="Check your daily items, add anything extra, then confirm and save.">
        <label>Date</label>
        <input type="date" value={day} max={todayISO()} onChange={(e) => setDay(e.target.value)} />
      </PageHeader>
      <ReadOnlyNotice />
      <Msg msg={msg} />

      {!ro && (
        <>
          <div className="card flush">
            <div className="card-head">
              <div>
                <h3>Daily items</h3>
                <p>Untick anything you didn’t buy on this day.</p>
              </div>
              <Link to="/items" className="btn secondary small">
                Manage items
              </Link>
            </div>
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}></th>
                    <th>Item</th>
                    <th>Category</th>
                    <th>Type</th>
                    <th className="num">Qty</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.id} className={r.include ? '' : 'dim'}>
                      <td>
                        <input type="checkbox" checked={r.include} onChange={(e) => updateRow(i, { include: e.target.checked })} aria-label={`Include ${r.name}`} />
                      </td>
                      <td className="strong">
                        {r.name} {r.alreadySaved && <span className="badge">already saved</span>}
                      </td>
                      <td>{r.category}</td>
                      <td>
                        <span className={`badge ${r.is_constant ? 'info' : ''}`}>{r.is_constant ? 'Constant' : 'Custom'}</span>
                      </td>
                      <td className="num">
                        <input className="w-num" type="number" min="1" value={r.quantity} onChange={(e) => updateRow(i, { quantity: e.target.value })} />
                      </td>
                      <td className="num">
                        {r.is_constant ? (
                          <b>{money(r.amount)}</b>
                        ) : (
                          <input className="w-amt" type="number" min="0" step="0.01" value={r.amount} onChange={(e) => updateRow(i, { amount: e.target.value })} />
                        )}
                      </td>
                    </tr>
                  ))}
                  {!rows.length && (
                    <EmptyRow cols={6}>
                      You have no daily items yet. <Link to="/items">Set up your daily items</Link> such as cigarettes, chocolate or food.
                    </EmptyRow>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div>
                <h3>Extra items</h3>
                <p>Anything that isn’t on your daily list.</p>
              </div>
              <button className="btn secondary small" onClick={() => setExtras([...extras, emptyExtra()])}>
                <Plus size={14} /> Add extra item
              </button>
            </div>
            {extras.map((x, i) => (
              <div className="extra-row" key={i}>
                <input placeholder="Item name" value={x.item_name} onChange={(e) => updateExtra(i, { item_name: e.target.value })} />
                <select value={x.category} onChange={(e) => updateExtra(i, { category: e.target.value })}>
                  {categories.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <input className="w-num" type="number" min="1" value={x.quantity} onChange={(e) => updateExtra(i, { quantity: e.target.value })} aria-label="Quantity" />
                <input className="w-amt" type="number" min="0" step="0.01" placeholder="Amount" value={x.amount} onChange={(e) => updateExtra(i, { amount: e.target.value })} />
                <input placeholder="Note (optional)" value={x.note} onChange={(e) => updateExtra(i, { note: e.target.value })} />
                <button className="btn ghost small" onClick={() => setExtras(extras.filter((_, idx) => idx !== i))} aria-label="Remove">
                  <X size={16} />
                </button>
              </div>
            ))}
            {!extras.length && <p className="muted" style={{ margin: 0 }}>No extra items added.</p>}
          </div>

          <div className="card row-between">
            <div>
              <div className="tile-label">Total to save</div>
              <div className="tile-value">{money(total)}</div>
            </div>
            <div className="filters" style={{ alignItems: 'flex-end' }}>
            <AccountSelect accounts={accounts} value={accountId} onChange={setAccountId} />
            <button className="btn" onClick={save} disabled={!selected.length && !validExtras.length}>
              <CheckCircle2 size={16} /> Confirm &amp; Save
            </button>
            </div>
          </div>
        </>
      )}

      <div className="card flush">
        <div className="card-head">
          <div>
            <h3>Saved for {day}</h3>
            <p>
              {money(savedTotal)} in {saved.length} entries. To edit or delete one, go to <Link to="/expenses">Expenses</Link>.
            </p>
          </div>
        </div>
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Category</th>
                <th className="num">Qty</th>
                <th className="num">Amount</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {saved.map((e) => (
                <tr key={e.id}>
                  <td className="strong">
                    {e.item_name} {e.is_extra && <span className="badge">extra</span>}
                  </td>
                  <td>{e.category}</td>
                  <td className="num">{e.quantity}</td>
                  <td className="num strong">{money(e.amount)}</td>
                  <td>
                    {e.locked ? (
                      <span className="badge">
                        <Lock size={12} /> Locked
                      </span>
                    ) : (
                      <span className="badge ok">Editable until {e.lock_at.replace('T', ' ').slice(0, 16)}</span>
                    )}
                  </td>
                </tr>
              ))}
              {!saved.length && <EmptyRow cols={5}>Nothing saved for this day yet.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
