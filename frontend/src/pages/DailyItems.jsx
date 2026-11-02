import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, Power, Trash2 } from 'lucide-react'
import api, { errorMessage, money } from '../api'
import { useAuth } from '../auth'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'

const blank = { name: '', category: 'General', default_amount: '', default_quantity: 1, is_constant: true, is_active: true, sort_order: 0 }

export default function DailyItems() {
  const { params, target } = useAuth()
  const [items, setItems] = useState([])
  const [categories, setCategories] = useState([])
  const [form, setForm] = useState(blank)
  const [editId, setEditId] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [r, c] = await Promise.all([api.get('/daily-items', { params }), api.get('/expenses/categories', { params })])
    setItems(r.data)
    setCategories(c.data)
  }, [params])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const submit = async (e) => {
    e.preventDefault()
    const body = { ...form, default_amount: Number(form.default_amount || 0), default_quantity: Number(form.default_quantity), sort_order: Number(form.sort_order) }
    try {
      if (editId) await api.put(`/daily-items/${editId}`, body)
      else await api.post('/daily-items', body, { params })
      setForm(blank)
      setEditId(null)
      setMsg({ type: 'ok', text: editId ? 'Item updated' : 'Item added' })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const toggleActive = async (it) => {
    await api.put(`/daily-items/${it.id}`, { ...it, is_active: !it.is_active })
    load()
  }

  const remove = async (it) => {
    if (!window.confirm(`Remove "${it.name}" from your daily items? Expenses you already saved are not changed.`)) return
    await api.delete(`/daily-items/${it.id}`)
    load()
  }

  const ro = !target?.writable
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  return (
    <div>
      <PageHeader
        title="My Daily Items"
        subtitle="Things you buy every day. Constant items keep a fixed price, and Custom items let you change the amount each day."
      />
      <ReadOnlyNotice />
      <Msg msg={msg} />

      {!ro && (
        <form className="card" onSubmit={submit}>
          <div className="card-head">
            <h3>{editId ? 'Edit item' : 'Add a daily item'}</h3>
          </div>
          <div className="form-grid">
            <div>
              <label>Name</label>
              <input value={form.name} onChange={set('name')} placeholder="e.g. Cigarette" required />
            </div>
            <div>
              <label>Category</label>
              <input list="cats" value={form.category} onChange={set('category')} required />
              <datalist id="cats">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div>
              <label>Price per unit</label>
              <input type="number" min="0" step="0.01" value={form.default_amount} onChange={set('default_amount')} required />
            </div>
            <div>
              <label>Default quantity</label>
              <input type="number" min="1" value={form.default_quantity} onChange={set('default_quantity')} />
            </div>
            <div>
              <label>Display order</label>
              <input type="number" value={form.sort_order} onChange={set('sort_order')} />
            </div>
            <div className="checks">
              <label className="check">
                <input type="checkbox" checked={form.is_constant} onChange={set('is_constant')} /> Constant price
              </label>
              <label className="check">
                <input type="checkbox" checked={form.is_active} onChange={set('is_active')} /> Active
              </label>
            </div>
          </div>
          <div className="form-actions">
            {editId && (
              <button type="button" className="btn secondary" onClick={() => { setEditId(null); setForm(blank) }}>
                Cancel
              </button>
            )}
            <button className="btn">
              {editId ? 'Update item' : <><Plus size={16} /> Add item</>}
            </button>
          </div>
        </form>
      )}

      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Category</th>
                <th className="num">Price</th>
                <th className="num">Qty</th>
                <th>Type</th>
                <th>Status</th>
                {!ro && <th></th>}
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className={it.is_active ? '' : 'dim'}>
                  <td className="strong">{it.name}</td>
                  <td>{it.category}</td>
                  <td className="num">{money(it.default_amount)}</td>
                  <td className="num">{it.default_quantity}</td>
                  <td>
                    <span className={`badge ${it.is_constant ? 'info' : ''}`}>{it.is_constant ? 'Constant' : 'Custom'}</span>
                  </td>
                  <td>
                    <span className={`badge ${it.is_active ? 'ok' : ''}`}>{it.is_active ? 'Active' : 'Inactive'}</span>
                  </td>
                  {!ro && (
                    <td>
                      <div className="actions">
                        <button className="btn ghost small" title="Edit" onClick={() => { setEditId(it.id); setForm({ ...it }); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
                          <Pencil size={14} />
                        </button>
                        <button className="btn ghost small" title={it.is_active ? 'Disable' : 'Enable'} onClick={() => toggleActive(it)}>
                          <Power size={14} />
                        </button>
                        <button className="btn ghost small" title="Delete" onClick={() => remove(it)} style={{ color: 'var(--danger)' }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
              {!items.length && <EmptyRow cols={7}>No daily items yet. Add the things you buy every day.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
