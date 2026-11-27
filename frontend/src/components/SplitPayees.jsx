import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, Store, Trash2 } from 'lucide-react'
import api, { errorMessage } from '../api'
import Modal from './Modal'
import { Msg } from './ui'

const EMPTY = { label: '', vpa: '', name_match: '', default_member_ids: [], is_active: true }

/**
 * Split group → Members: the shops and people this group pays together (e.g. the DMart the flatmates shop at).
 * When you pay one of them by UPI, the app asks "Split this with <group>?". Each member keeps their own list: it
 * matches only their own payments, and only these payees, so nothing else in their records changes.
 */
export default function SplitPayees({ group }) {
  const [payees, setPayees] = useState([])
  const [edit, setEdit] = useState(null)
  const [msg, setMsg] = useState(null)
  const members = group.members.filter((m) => m.active).map((m) => m.member)

  const load = useCallback(() => api.get(`/splits/groups/${group.id}/payees`).then((r) => setPayees(r.data)), [group.id])
  useEffect(() => {
    load().catch(() => {})
  }, [load])

  const remove = async (p) => {
    if (!window.confirm(`Stop asking to split payments to ${p.label}? Bills already split stay.`)) return
    try {
      await api.delete(`/splits/payees/${p.id}`)
      setMsg({ type: 'ok', text: `${p.label} removed` })
      load()
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3>Shared shops and UPI IDs</h3>
          <p>
            Paid one of these by UPI? Expense Tracker asks whether to split it with this group, with everyone’s share ready to change. Only your own
            payments to these UPI IDs are asked about; other payments never are. Needs auto-tracking on your phone.
          </p>
        </div>
        <button className="btn small" onClick={() => setEdit(EMPTY)}>
          <Plus size={14} /> Add
        </button>
      </div>
      <Msg msg={msg} />
      {payees.length ? (
        <ul className="list">
          {payees.map((p) => (
            <li key={p.id}>
              <div className="cell-user">
                <Store size={20} className="muted" />
                <div>
                  <b>{p.label}</b>
                  <small>
                    {[p.vpa, p.name_match && `name has “${p.name_match}”`].filter(Boolean).join(' · ')}
                    {!p.is_active && ' · paused'}
                  </small>
                </div>
              </div>
              <div className="actions">
                <button className="btn ghost small" title="Change" onClick={() => setEdit(p)}>
                  <Pencil size={14} />
                </button>
                <button className="btn ghost small" title="Remove" style={{ color: 'var(--danger)' }} onClick={() => remove(p)}>
                  <Trash2 size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None yet. Add the UPI ID of a shop you all buy from (it’s on the shop’s QR code, or in your UPI app’s payment details).</p>
      )}
      {edit && (
        <PayeeForm
          group={group}
          members={members}
          payee={edit}
          onClose={() => setEdit(null)}
          onSaved={(text) => {
            setEdit(null)
            setMsg({ type: 'ok', text })
            load()
          }}
        />
      )}
    </div>
  )
}

function PayeeForm({ group, members, payee, onClose, onSaved }) {
  const [f, setF] = useState({
    ...payee,
    vpa: payee.vpa || '',
    name_match: payee.name_match || '',
    // Someone who has left since is no longer offered
    default_member_ids: payee.default_member_ids.filter((id) => members.some((m) => m.id === id)),
  })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const everyone = !f.default_member_ids.length
  const ticked = (id) => everyone || f.default_member_ids.includes(id)
  const toggle = (id) => {
    const cur = everyone ? members.map((m) => m.id) : f.default_member_ids
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    setF({ ...f, default_member_ids: next.length === members.length ? [] : next })
  }

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    const body = { label: f.label, vpa: f.vpa.trim() || null, name_match: f.name_match.trim() || null, default_member_ids: f.default_member_ids, is_active: f.is_active }
    try {
      if (payee.id) await api.put(`/splits/payees/${payee.id}`, body)
      else await api.post(`/splits/groups/${group.id}/payees`, body)
      onSaved(`${f.label} saved. Payments to it will ask to split with ${group.name}.`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={payee.id ? 'Change shared payee' : 'Add a shared shop or UPI ID'} subtitle={group.name} onClose={onClose}>
      <form onSubmit={save}>
        {error && <Msg msg={{ type: 'error', text: error }} />}
        <div className="form-grid">
          <div className="span-all">
            <label htmlFor="sp-label">Name</label>
            <input id="sp-label" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} maxLength={100} required placeholder="e.g. DMart groceries" />
          </div>
          <div>
            <label htmlFor="sp-vpa">UPI ID</label>
            <input id="sp-vpa" value={f.vpa} onChange={(e) => setF({ ...f, vpa: e.target.value })} maxLength={100} placeholder="e.g. dmart.blr@icici"
              autoCapitalize="none" autoCorrect="off" spellCheck={false} />
          </div>
          <div>
            <label htmlFor="sp-word">Or a word in its name</label>
            <input id="sp-word" value={f.name_match} onChange={(e) => setF({ ...f, name_match: e.target.value })} maxLength={60} placeholder="e.g. DMART" />
            <span className="hint">Fill this in too if you pay with Google Pay, PhonePe or Paytm: their notifications show the shop’s name, not its UPI ID. Use a word from the name as the app shows it after you pay.</span>
          </div>
          <div className="span-all">
            <label>Split with, to begin with</label>
            <div className="stack" style={{ gap: 4 }}>
              {members.map((m) => (
                <label key={m.id} className="check">
                  <input type="checkbox" checked={ticked(m.id)} onChange={() => toggle(m.id)} /> {m.full_name}
                  {m.is_guest && <span className="muted small"> (guest)</span>}
                </label>
              ))}
            </div>
            <span className="hint">You can still change who shares, and the amounts, each time.</span>
          </div>
          {payee.id && (
            <label className="check span-all">
              <input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Ask about payments to it
            </label>
          )}
        </div>
        <div className="form-actions">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" disabled={busy || (!f.vpa.trim() && !f.name_match.trim())}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
