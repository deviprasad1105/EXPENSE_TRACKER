import { Fragment, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import api, { errorMessage } from '../api'
import Modal from '../components/Modal'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { Msg, PageHeader } from '../components/ui'
import { DrCr, useBooks } from './common'

const NOMINAL = new Set(['SALES', 'PURCHASE', 'DIRECT_INCOMES', 'DIRECT_EXPENSES', 'INDIRECT_INCOMES', 'INDIRECT_EXPENSES'])
const BLANK = { name: '', group_code: 'INDIRECT_EXPENSES', opening_balance: '', opening_side: 'DR', is_active: true, is_depreciation: false, gstin: '', state: '' }

export default function Ledgers() {
  const books = useBooks()
  const { params, settings, meta, ledgers } = books
  const [editing, setEditing] = useState(null)
  const [msg, setMsg] = useState(null)
  const [search, setSearch] = useState('')
  const writable = settings.data?.writable
  const groups = meta.data?.groups || []
  const groupName = Object.fromEntries(groups.map((g) => [g.code, g.name]))

  const q = search.trim().toLowerCase()
  const shown = (ledgers.data || []).filter((g) => !q || g.name.toLowerCase().includes(q) || groupName[g.group_code]?.toLowerCase().includes(q))
  const byGroup = {}
  for (const g of shown) (byGroup[g.group_code] ||= []).push(g)

  const remove = async (g) => {
    if (!window.confirm(`Delete the ledger “${g.name}”?`)) return
    try {
      await api.delete(`/accounting/ledgers/${g.id}`, { params })
      setMsg({ type: 'ok', text: `${g.name} deleted` })
      ledgers.reload()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  return (
    <div>
      <PageHeader title="Chart of Accounts" subtitle="Tally’s standard groups with your ledgers. Bank accounts and split groups get their own ledgers automatically.">
        <input placeholder="Find a ledger" value={search} onChange={(e) => setSearch(e.target.value)} />
        {writable && (
          <button className="btn" onClick={() => setEditing({ ...BLANK })}>
            <Plus size={16} /> New ledger
          </button>
        )}
      </PageHeader>
      <ReadOnlyNotice />
      <Msg msg={msg} />
      {(ledgers.error || settings.error) && <Msg msg={{ type: 'error', text: ledgers.error || settings.error }} />}

      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Ledger</th>
                <th className="num">Opening balance</th>
                <th className="num">Balance today</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {groups
                .filter((gr) => byGroup[gr.code])
                .map((gr) => (
                  <Fragment key={gr.code}>
                    <tr className="group-row">
                      <td colSpan={4}>
                        {gr.parent ? <span className="muted">{groupName[gr.parent]} › </span> : null}
                        {gr.name}
                      </td>
                    </tr>
                    {byGroup[gr.code].map((g) => (
                      <tr key={g.id} className={g.is_active ? '' : 'dim'}>
                        <td style={{ paddingLeft: 32 }}>
                          <span className="strong">{g.name}</span>
                          {g.account_id && <span className="badge info" style={{ marginLeft: 6 }}>Bank account</span>}
                          {g.split_group_id && <span className="badge info" style={{ marginLeft: 6 }}>Split group</span>}
                          {g.code && <span className="badge" style={{ marginLeft: 6 }}>Standard</span>}
                          {g.is_depreciation && <span className="badge" style={{ marginLeft: 6 }}>Depreciation</span>}
                          {!g.is_active && <span className="badge warn" style={{ marginLeft: 6 }}>Inactive</span>}
                          {g.gstin && <span className="muted small"> · GSTIN {g.gstin}</span>}
                        </td>
                        <td className="num"><DrCr amount={g.opening_balance} side={g.opening_side} /></td>
                        <td className="num strong"><DrCr amount={g.balance} side={g.balance_side} /></td>
                        <td>
                          {writable && (
                            <div className="actions">
                              <button className="btn ghost small" title="Edit" onClick={() => setEditing({ ...g, opening_balance: Number(g.opening_balance) ? g.opening_balance : '', gstin: g.gstin || '', state: g.state || '' })}>
                                <Pencil size={14} />
                              </button>
                              {!g.system && !g.used && (
                                <button className="btn ghost small" title="Delete" onClick={() => remove(g)} style={{ color: 'var(--danger)' }}>
                                  <Trash2 size={14} />
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <LedgerForm
          ledger={editing}
          groups={groups}
          states={meta.data?.states || []}
          locked={!!settings.data?.lock_date}
          params={params}
          onClose={() => setEditing(null)}
          onSaved={(g) => {
            setEditing(null)
            setMsg({ type: 'ok', text: `${g.name} saved` })
            ledgers.reload()
          }}
        />
      )}
    </div>
  )
}

function LedgerForm({ ledger, groups, states, locked, params, onClose, onSaved }) {
  const [form, setForm] = useState(ledger)
  const [msg, setMsg] = useState(null)
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const linked = form.account_id || form.split_group_id
  const nominal = NOMINAL.has(form.group_code)
  const party = ['SUNDRY_DEBTORS', 'SUNDRY_CREDITORS'].includes(form.group_code)

  const save = async (e) => {
    e.preventDefault()
    setSaving(true)
    const body = {
      name: form.name,
      group_code: form.group_code,
      opening_balance: nominal ? '0' : form.opening_balance || '0',
      opening_side: form.opening_side,
      is_active: form.is_active,
      is_depreciation: form.is_depreciation,
      gstin: form.gstin || null,
      state: form.state || null,
    }
    try {
      const res = form.id
        ? await api.put(`/accounting/ledgers/${form.id}`, body, { params })
        : await api.post('/accounting/ledgers', body, { params })
      onSaved(res.data)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      setSaving(false)
    }
  }

  return (
    <Modal title={form.id ? `Edit ${ledger.name}` : 'New ledger'} onClose={onClose}>
      <form onSubmit={save} className="stack" style={{ gap: 14 }}>
        <Msg msg={msg} />
        <div className="form-grid">
          <div className="span-all">
            <label>Name</label>
            <input value={form.name} onChange={set('name')} maxLength={100} required />
          </div>
          <div className="span-all">
            <label>Under group</label>
            <select value={form.group_code} onChange={set('group_code')} disabled={!!(linked || form.code)}>
              {groups.map((g) => (
                <option key={g.code} value={g.code}>
                  {g.parent ? '   ' : ''}{g.name}
                </option>
              ))}
            </select>
            {linked && <span className="hint">Follows its bank account / split group: change the type and opening balance there.</span>}
          </div>
          {!nominal && (
            <>
              <div>
                <label>Opening balance</label>
                <input type="number" min="0" step="0.01" value={form.opening_balance} onChange={set('opening_balance')} disabled={!!linked || locked} />
              </div>
              <div>
                <label>Dr / Cr</label>
                <select value={form.opening_side} onChange={set('opening_side')} disabled={!!linked || locked}>
                  <option value="DR">Dr (asset, expense)</option>
                  <option value="CR">Cr (liability, capital)</option>
                </select>
              </div>
              {locked && <span className="hint span-all">The books are locked, so opening balances can’t change.</span>}
            </>
          )}
          {party && (
            <>
              <div>
                <label>GSTIN</label>
                <input value={form.gstin} onChange={(e) => setForm({ ...form, gstin: e.target.value.toUpperCase() })} maxLength={15} placeholder="27ABCDE1234F1Z5" />
              </div>
              <div>
                <label>State</label>
                <select value={form.state} onChange={set('state')}>
                  <option value="">—</option>
                  {states.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </div>
            </>
          )}
          <div className="span-all stack">
            {!form.code && (
              <label className="check">
                <input type="checkbox" checked={form.is_active} onChange={set('is_active')} /> Active
              </label>
            )}
            {form.group_code === 'INDIRECT_EXPENSES' && (
              <label className="check">
                <input type="checkbox" checked={form.is_depreciation} onChange={set('is_depreciation')} /> Depreciation (added back in the cash flow statement)
              </label>
            )}
          </div>
        </div>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Modal>
  )
}
