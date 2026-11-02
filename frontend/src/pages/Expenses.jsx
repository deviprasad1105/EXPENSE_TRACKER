import { useCallback, useEffect, useState } from 'react'
import { Check, Lock, Pencil, Search, Trash2, X } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'

const monthStart = () => todayISO().slice(0, 8) + '01'

export default function Expenses() {
  const { params, target, user } = useAuth()
  const [filters, setFilters] = useState({ start: monthStart(), end: todayISO(), category: '', search: '' })
  const [list, setList] = useState([])
  const [categories, setCategories] = useState([])
  const [editing, setEditing] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const q = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
    const [res, cats] = await Promise.all([api.get('/expenses', { params: { ...params, ...q } }), api.get('/expenses/categories', { params })])
    setList(res.data)
    setCategories(cats.data)
  }, [params, filters])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const saveEdit = async () => {
    try {
      const { id, expense_date, item_name, category, quantity, amount, note } = editing
      await api.put(`/expenses/${id}`, { expense_date, item_name, category, quantity: Number(quantity), amount: Number(amount), note })
      setEditing(null)
      setMsg({ type: 'ok', text: 'Expense updated' })
      load()
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    }
  }

  const remove = async (e) => {
    if (!window.confirm(`Delete "${e.item_name}" (${money(e.amount)}) on ${e.expense_date}?`)) return
    try {
      await api.delete(`/expenses/${e.id}`)
      setMsg({ type: 'ok', text: user.role === 'ADMIN' ? 'Expense deleted. A backup was saved (Administration → Backups).' : 'Expense deleted' })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const total = list.reduce((s, e) => s + Number(e.amount), 0)
  const set = (k) => (e) => setFilters({ ...filters, [k]: e.target.value })
  const ed = (k) => (ev) => setEditing({ ...editing, [k]: ev.target.value })

  return (
    <div>
      <PageHeader title="Expenses" subtitle="Every saved expense. You can edit an entry for 3 days after it was saved." />
      <ReadOnlyNotice />
      <Msg msg={msg} />

      <div className="card flush">
        <div className="toolbar">
          <input type="date" value={filters.start} onChange={set('start')} aria-label="From" />
          <span className="muted">to</span>
          <input type="date" value={filters.end} onChange={set('end')} aria-label="To" />
          <select value={filters.category} onChange={set('category')}>
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <div style={{ position: 'relative' }}>
            <Search size={16} className="muted" style={{ position: 'absolute', left: 10, top: 11 }} />
            <input placeholder="Search items" value={filters.search} onChange={set('search')} style={{ paddingLeft: 32 }} />
          </div>
          <span className="spacer" />
          <span className="muted">
            {list.length} entries · <b style={{ color: 'var(--text)' }}>{money(total)}</b>
          </span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Item</th>
                <th>Category</th>
                <th className="num">Qty</th>
                <th className="num">Amount</th>
                <th>Note</th>
                <th>Status</th>
                {target?.writable && <th></th>}
              </tr>
            </thead>
            <tbody>
              {list.map((e) =>
                editing?.id === e.id ? (
                  <tr key={e.id} className="editing">
                    <td><input type="date" value={editing.expense_date} max={todayISO()} onChange={ed('expense_date')} /></td>
                    <td><input value={editing.item_name} onChange={ed('item_name')} /></td>
                    <td>
                      <select value={editing.category} onChange={ed('category')}>
                        {categories.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    </td>
                    <td className="num"><input className="w-num" type="number" min="1" value={editing.quantity} onChange={ed('quantity')} /></td>
                    <td className="num"><input className="w-amt" type="number" step="0.01" value={editing.amount} onChange={ed('amount')} /></td>
                    <td><input value={editing.note || ''} onChange={ed('note')} /></td>
                    <td colSpan={2}>
                      <div className="actions">
                        <button className="btn small" onClick={saveEdit}><Check size={14} /> Save</button>
                        <button className="btn secondary small" onClick={() => setEditing(null)}><X size={14} /> Cancel</button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={e.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{e.expense_date}</td>
                    <td className="strong">
                      {e.item_name} {e.is_extra && <span className="badge">extra</span>}
                    </td>
                    <td>{e.category}</td>
                    <td className="num">{e.quantity}</td>
                    <td className="num strong">{money(e.amount)}</td>
                    <td className="muted">{e.note}</td>
                    <td>
                      {e.locked ? (
                        <span className="badge" title={`Locked since ${e.lock_at.replace('T', ' ')}`}>
                          <Lock size={12} /> Locked
                        </span>
                      ) : (
                        <span className="badge ok">Editable until {e.lock_at.slice(0, 10)}</span>
                      )}
                    </td>
                    {target?.writable && (
                      <td>
                        {e.editable && (
                          <div className="actions">
                            <button className="btn ghost small" onClick={() => setEditing({ ...e })} title="Edit"><Pencil size={14} /></button>
                            <button className="btn ghost small" onClick={() => remove(e)} title="Delete" style={{ color: 'var(--danger)' }}><Trash2 size={14} /></button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ),
              )}
              {!list.length && <EmptyRow cols={8}>No expenses match these filters.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
