import { useMemo, useState } from 'react'
import { Save } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import Modal from './Modal'
import { Msg } from './ui'

/** A group member (app user or guest) by name, "You" for the signed-in user. */
export const personName = (p, me) => (p.user_id === me.id ? 'You' : p.full_name)
export const handle = (p) => (p.is_guest ? 'guest' : `@${p.username}`)

const TYPES = [
  ['EQUAL', 'Equal share'],
  ['PERCENT', 'Percentage'],
  ['AMOUNT', 'Fixed amount'],
]

/** Same rules as the server: fixed amounts and percentages first, the rest divided equally. */
export function previewShares(total, rows) {
  const on = rows.filter((r) => r.included)
  let fixed = 0
  const amounts = {}
  for (const r of on) {
    if (r.share_type === 'EQUAL') continue
    const v = Number(r.share_value) || 0
    amounts[r.person.id] = r.share_type === 'PERCENT' ? (total * v) / 100 : v
    fixed += amounts[r.person.id]
  }
  const equal = on.filter((r) => r.share_type === 'EQUAL')
  const rest = total - fixed
  for (const r of equal) amounts[r.person.id] = Math.max(rest, 0) / equal.length
  let problem = null
  if (!on.length) problem = 'Pick at least one person.'
  else if (rest < -0.009) problem = `Fixed shares add up to ${money(fixed)}, more than the bill.`
  else if (!equal.length && Math.abs(rest) > 0.009)
    problem = `Shares add up to ${money(fixed)}, but the bill is ${money(total)}. ${money(rest)} is not assigned to anyone.`
  return { amounts, problem }
}

/**
 * Add or edit a bill. Everyone is a group member (app user or guest), identified by member id; when editing,
 * people who have left the group but are still on the bill are kept.
 */
export default function BillForm({ group, bill, onClose, onSaved }) {
  const { user } = useAuth()
  const people = useMemo(() => {
    const list = group.members.filter((m) => m.active).map((m) => m.member)
    const keep = [...(bill?.shares || []).map((s) => s.member), ...(bill ? [bill.paid_by] : [])]
    for (const p of keep) if (!list.some((x) => x.id === p.id)) list.push(p)
    return list
  }, [group, bill])

  const [form, setForm] = useState({
    title: bill?.title || '',
    total_amount: bill ? String(bill.total_amount) : '',
    bill_date: bill?.bill_date || todayISO(),
    paid_by: bill?.paid_by.id || group.me.member_id,
    note: bill?.note || '',
  })
  const [rows, setRows] = useState(() =>
    people.map((p) => {
      const s = bill?.shares.find((x) => x.member.id === p.id)
      return {
        person: p,
        included: bill ? !!s : true,
        share_type: s?.share_type || 'EQUAL',
        share_value: s?.share_value != null ? String(Number(s.share_value)) : '',
      }
    }),
  )
  const [msg, setMsg] = useState(null)
  const [saving, setSaving] = useState(false)

  const total = Number(form.total_amount) || 0
  const { amounts, problem } = previewShares(total, rows)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const setRow = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const submit = async (e) => {
    e.preventDefault()
    if (problem) return setMsg({ type: 'error', text: problem })
    const body = {
      ...form,
      title: form.title.trim(),
      total_amount: Number(form.total_amount),
      paid_by: Number(form.paid_by),
      note: form.note || null,
      shares: rows
        .filter((r) => r.included)
        .map((r) => ({
          member_id: r.person.id,
          share_type: r.share_type,
          share_value: r.share_type === 'EQUAL' ? null : Number(r.share_value) || 0,
        })),
    }
    setSaving(true)
    try {
      if (bill) await api.put(`/splits/bills/${bill.id}`, body)
      else await api.post(`/splits/groups/${group.id}/bills`, body)
      onSaved(bill ? 'Bill updated. Everyone in the group was notified.' : 'Bill added. Everyone in the group was notified.')
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      setSaving(false)
    }
  }

  return (
    <Modal wide title={bill ? `Edit “${bill.title}”` : 'Add a bill'} subtitle="Give people a fixed amount or percentage; everyone on an equal share splits what’s left." onClose={onClose}>
      <form onSubmit={submit}>
        <Msg msg={msg} />
        <div className="form-grid">
          <div>
            <label>What was it for?</label>
            <input value={form.title} onChange={set('title')} maxLength={100} required autoFocus />
          </div>
          <div>
            <label>Total amount</label>
            <input type="number" min="0.01" step="0.01" value={form.total_amount} onChange={set('total_amount')} required />
          </div>
          <div>
            <label>Date</label>
            <input type="date" max={todayISO()} value={form.bill_date} onChange={set('bill_date')} required />
          </div>
          <div>
            <label>Paid by</label>
            <select value={form.paid_by} onChange={set('paid_by')}>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {personName(p, user)}
                </option>
              ))}
            </select>
          </div>
          <div className="span-all">
            <label>Note</label>
            <input value={form.note} onChange={set('note')} maxLength={500} />
          </div>
        </div>

        <h3 style={{ margin: '20px 0 4px' }}>Split between</h3>
        <div>
          {rows.map((r, i) => (
            <div key={r.person.id} className={`share-row ${r.included ? '' : 'off'}`}>
              <input type="checkbox" checked={r.included} onChange={(e) => setRow(i, { included: e.target.checked })} aria-label={`Include ${r.person.full_name}`} />
              <span>
                {personName(r.person, user)} <span className="muted small">{handle(r.person)}</span>
              </span>
              <select value={r.share_type} disabled={!r.included} onChange={(e) => setRow(i, { share_type: e.target.value })}>
                {TYPES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              {r.share_type === 'EQUAL' ? (
                <span className="muted small">of the rest</span>
              ) : (
                <input
                  type="number"
                  min="0"
                  step={r.share_type === 'PERCENT' ? '0.0001' : '0.01'}
                  max={r.share_type === 'PERCENT' ? 100 : undefined}
                  placeholder={r.share_type === 'PERCENT' ? '%' : '₹'}
                  value={r.share_value}
                  disabled={!r.included}
                  onChange={(e) => setRow(i, { share_value: e.target.value })}
                  required={r.included}
                />
              )}
              <span className="amt">{r.included ? money(amounts[r.person.id] || 0) : '—'}</span>
            </div>
          ))}
        </div>
        {problem && total > 0 && (
          <p className="small" style={{ color: 'var(--danger)' }}>
            {problem}
          </p>
        )}
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" disabled={saving}>
            <Save size={16} /> {bill ? 'Save changes' : 'Add bill'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
