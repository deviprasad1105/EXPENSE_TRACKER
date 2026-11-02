import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  Crown,
  HandCoins,
  LogOut,
  Pencil,
  Plus,
  Receipt,
  Scale,
  ShieldMinus,
  ShieldPlus,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
  Wallet,
} from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import BillForm, { handle, personName as name } from '../components/BillForm'
import Modal from '../components/Modal'
import { Alert, EmptyRow, Msg, PageHeader, StatTile } from '../components/ui'

const SHARE_LABEL = { EQUAL: 'equal', PERCENT: '%', AMOUNT: 'fixed' }

const shareText = (s) =>
  s.share_type === 'PERCENT' ? `${Number(s.share_value)}%` : s.share_type === 'AMOUNT' ? 'fixed' : 'equal'

const userName = (u, me) => (u.id === me.id ? 'You' : u.full_name)
const avatarOf = (p) => ({ id: p.user_id ?? p.id, full_name: p.full_name, avatar_updated_at: p.avatar_updated_at })

/** Per bill, what everyone paid before and after a member was added or removed. */
function ChangeSummary({ bills, me }) {
  return bills.map((b) => (
    <div key={b.bill_id} className="summary-bill">
      <div className="row-between">
        <b>{b.title}</b>
        <span className="muted small">Bill total {money(b.total_amount)}</span>
      </div>
      {b.warning && <div className="small" style={{ color: 'var(--warning)' }}>{b.warning}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>Person</th>
            <th className="num">Before</th>
            <th className="num">After</th>
            <th className="num">Change</th>
          </tr>
        </thead>
        <tbody>
          {b.rows.map((r) => {
            const diff = Number(r.after ?? 0) - Number(r.before ?? 0)
            return (
              <tr key={r.member.id}>
                <td className={r.is_target ? 'strong' : ''}>{name(r.member, me)}</td>
                <td className="num">{r.before == null ? '—' : money(r.before)}</td>
                <td className="num">{r.after == null ? 'removed' : money(r.after)}</td>
                <td className={`num ${diff > 0.001 ? 'net-neg' : diff < -0.001 ? 'net-pos' : 'muted'}`}>
                  {Math.abs(diff) < 0.001 ? '—' : `${diff > 0 ? '+' : '−'}${money(Math.abs(diff))}`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  ))
}

function AddMember({ group, me, onClose, onDone }) {
  const [person, setPerson] = useState('')
  const [picked, setPicked] = useState([])
  const [result, setResult] = useState(null)
  const [msg, setMsg] = useState(null)
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  const submit = async (e) => {
    e.preventDefault()
    try {
      const res = await api.post(`/splits/groups/${group.id}/members`, { name: person.trim(), include_bill_ids: picked })
      setResult(res.data)
      onDone()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  if (result)
    return (
      <Modal wide title={`${result.member.full_name} was added`} onClose={onClose}>
        {result.bills.length ? (
          <>
            <p className="muted">
              They now share {result.bills.length} existing bill(s). Fixed amounts and percentages stayed the same; the equal shares were
              recalculated.
            </p>
            <ChangeSummary bills={result.bills} me={me} />
          </>
        ) : (
          <p className="muted">They’ll be included in new bills. No existing bills were changed.</p>
        )}
        <div className="form-actions">
          <button className="btn" onClick={onClose}>
            Done
          </button>
        </div>
      </Modal>
    )

  return (
    <Modal title="Add a member" subtitle="Everyone in the group is notified." onClose={onClose}>
      <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
        <Msg msg={msg} />
        <div className="field">
          <label>Who</label>
          <input
            value={person}
            onChange={(e) => setPerson(e.target.value)}
            placeholder={group.me.can_add_guests ? '@username, or a name' : '@username'}
            required
            autoFocus
          />
          <span className="hint">
            {group.me.can_add_guests
              ? 'Type @username for someone on the app. Type just a name for someone who isn’t: they’re only tracked here and can’t sign in.'
              : 'Type @username for someone on the app. Adding people who aren’t on the app is part of the split bills plan.'}
          </span>
        </div>
        {group.bills.length > 0 && (
          <div className="field">
            <label>Also add them to these bills (optional)</label>
            <ul className="check-list">
              {group.bills.map((b) => (
                <li key={b.id}>
                  <input type="checkbox" checked={picked.includes(b.id)} onChange={() => toggle(b.id)} id={`add-${b.id}`} />
                  <label htmlFor={`add-${b.id}`} className="grow" style={{ flex: 1, cursor: 'pointer' }}>
                    {b.title} <span className="muted small">· {b.bill_date}</span>
                  </label>
                  <span className="muted">{money(b.total_amount)}</span>
                </li>
              ))}
            </ul>
            <span className="hint">They join the equal share. Fixed amounts and percentages of the others stay as they are.</span>
          </div>
        )}
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">
            <UserPlus size={16} /> Add member
          </button>
        </div>
      </form>
    </Modal>
  )
}

function RemoveMember({ group, member, me, onClose, onDone }) {
  const person = member.member
  const self = person.user_id === me.id
  const bills = group.bills.filter((b) => b.shares.some((s) => s.member.id === person.id))
  const blocked = (b) =>
    b.paid_by.id === person.id ? 'They paid this bill' : b.shares.length === 1 ? 'Only person on the bill' : null
  const [picked, setPicked] = useState(() => bills.filter((b) => !blocked(b)).map((b) => b.id))
  const [preview, setPreview] = useState(null)
  const [done, setDone] = useState(null)
  const [msg, setMsg] = useState(null)
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  const url = `/splits/groups/${group.id}/members/${person.id}/remove`

  useEffect(() => {
    let live = true
    api
      .post(url, { exclude_bill_ids: picked, dry_run: true })
      .then((r) => live && (setPreview(r.data), setMsg(null)))
      .catch((e) => live && setMsg({ type: 'error', text: errorMessage(e) }))
    return () => {
      live = false
    }
  }, [url, picked])

  const confirm = async () => {
    try {
      const res = await api.post(url, { exclude_bill_ids: picked })
      setDone(res.data)
      onDone(self)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const summary = done || preview
  const who = self ? 'you' : person.full_name
  const remaining = summary ? Number(summary.remaining_net) : 0

  return (
    <Modal
      wide
      title={done ? (self ? 'You left the group' : `${person.full_name} was removed`) : self ? 'Leave this group' : `Remove ${person.full_name}`}
      subtitle={done ? 'Summary of what changed. Everyone in the group was notified.' : `Tick the bills ${who} should be taken out of. Their amount there is split between the others on the bill.`}
      onClose={onClose}
    >
      <Msg msg={msg} />
      {!done && (
        <ul className="check-list">
          {bills.map((b) => {
            const why = blocked(b)
            const mine = b.shares.find((s) => s.member.id === person.id)
            return (
              <li key={b.id} className={why ? 'disabled' : ''}>
                <input type="checkbox" disabled={!!why} checked={picked.includes(b.id)} onChange={() => toggle(b.id)} id={`rm-${b.id}`} />
                <label htmlFor={`rm-${b.id}`} style={{ flex: 1, cursor: why ? 'default' : 'pointer' }}>
                  {b.title} <span className="muted small">· {b.bill_date}{why ? ` · ${why}, stays on it` : ''}</span>
                </label>
                <span className="muted">their share {money(mine.amount)}</span>
              </li>
            )
          })}
          {!bills.length && <li className="muted">{self ? 'You are' : 'They are'} not on any bill.</li>}
        </ul>
      )}
      {summary && (
        <>
          {summary.bills.length > 0 && (
            <Alert type="info">
              {money(summary.moved_total)} that {who} {done ? 'no longer pay' : 'would no longer pay'} {done ? 'is now' : 'will be'} split between the
              other people on {summary.bills.length} bill(s).
            </Alert>
          )}
          <ChangeSummary bills={summary.bills} me={me} />
          {Math.abs(remaining) >= 0.01 && (
            <Alert type="warn">
              From the bills {self ? 'you stay' : 'they stay'} on, {who} {remaining < 0 ? `still owe${self ? '' : 's'} ${money(-remaining)}` : `${self ? 'are' : 'is'} still owed ${money(remaining)}`}.
              This stays open in the group until it’s settled.
            </Alert>
          )}
        </>
      )}
      <div className="form-actions">
        {done ? (
          <button className="btn" onClick={onClose}>
            Done
          </button>
        ) : (
          <>
            <button className="btn secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="btn danger" onClick={confirm} disabled={!preview}>
              {self ? <LogOut size={16} /> : <UserMinus size={16} />} {self ? 'Leave group' : 'Remove member'}
            </button>
          </>
        )}
      </div>
    </Modal>
  )
}

function SettleForm({ group, me, debt, onClose, onSaved }) {
  const myId = group.me.member_id
  const people = group.members.filter((m) => m.active || Math.abs(Number(m.net)) >= 0.01).map((m) => m.member)
  const [form, setForm] = useState({
    from_member_id: debt?.from.id ?? myId,
    to_member_id: debt?.to.id ?? people.find((p) => p.id !== myId)?.id ?? '',
    amount: debt ? String(Number(debt.amount)) : '',
    settled_on: todayISO(),
    note: '',
  })
  const [msg, setMsg] = useState(null)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    try {
      await api.post(`/splits/groups/${group.id}/settlements`, {
        ...form,
        from_member_id: Number(form.from_member_id),
        to_member_id: Number(form.to_member_id),
        amount: Number(form.amount),
        note: form.note || null,
      })
      onSaved('Payment recorded. Both people and the group were notified.')
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const options = people.map((p) => (
    <option key={p.id} value={p.id}>
      {name(p, me)}
      {p.is_guest ? ' (guest)' : ''}
    </option>
  ))
  return (
    <Modal title="Record a payment" subtitle="Money paid back between two people. You must be one of them, or record it for a guest you added." onClose={onClose}>
      <form onSubmit={submit}>
        <Msg msg={msg} />
        <div className="form-grid">
          <div>
            <label>Paid by</label>
            <select value={form.from_member_id} onChange={set('from_member_id')}>{options}</select>
          </div>
          <div>
            <label>Paid to</label>
            <select value={form.to_member_id} onChange={set('to_member_id')}>{options}</select>
          </div>
          <div>
            <label>Amount</label>
            <input type="number" min="0.01" step="0.01" value={form.amount} onChange={set('amount')} required />
          </div>
          <div>
            <label>Date</label>
            <input type="date" max={todayISO()} value={form.settled_on} onChange={set('settled_on')} required />
          </div>
          <div className="span-all">
            <label>Note</label>
            <input value={form.note} onChange={set('note')} maxLength={500} placeholder="UPI, cash…" />
          </div>
        </div>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">
            <HandCoins size={16} /> Record payment
          </button>
        </div>
      </form>
    </Modal>
  )
}

function EditGroup({ group, onClose, onSaved }) {
  const [form, setForm] = useState({ name: group.name, description: group.description || '' })
  const [msg, setMsg] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    try {
      await api.patch(`/splits/groups/${group.id}`, { name: form.name.trim(), description: form.description || null })
      onSaved('Group updated')
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }
  return (
    <Modal title="Edit group" onClose={onClose}>
      <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
        <Msg msg={msg} />
        <div className="field">
          <label>Group name</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={100} required />
        </div>
        <div className="field">
          <label>Description</label>
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={500} />
        </div>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">Save</button>
        </div>
      </form>
    </Modal>
  )
}

export default function SplitGroup() {
  const { groupId } = useParams()
  const navigate = useNavigate()
  const { user: me } = useAuth()
  const [group, setGroup] = useState(null)
  const [tab, setTab] = useState('bills')
  const [modal, setModal] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => setGroup((await api.get(`/splits/groups/${groupId}`)).data), [groupId])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const saved = (text) => {
    setModal(null)
    setMsg({ type: 'ok', text })
    load()
  }
  const act = async (fn, text) => {
    try {
      await fn()
      saved(text)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  if (!group)
    return msg ? (
      <div>
        <Msg msg={msg} />
        <Link to="/splits">← Back to groups</Link>
      </div>
    ) : (
      <div className="muted">Loading…</div>
    )

  const myId = group.me.member_id
  const mine = group.members.find((m) => m.member.id === myId)
  const total = group.bills.reduce((s, b) => s + Number(b.total_amount), 0)
  const { is_admin: isAdmin, is_owner: isOwner } = group.me
  const myNet = Number(mine?.net || 0)
  // Your own payments, or ones for a guest (group admins, or whoever added the guest)
  const canSettle = (d) =>
    d.from.id === myId || d.to.id === myId || [d.from, d.to].some((p) => p.is_guest && (isAdmin || p.added_by === me.id))

  const deleteGroup = () => {
    if (!window.confirm(`Delete “${group.name}” with all its bills and payments? This can’t be undone.`)) return
    api
      .delete(`/splits/groups/${group.id}`)
      .then(() => navigate('/splits'))
      .catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }

  return (
    <div>
      <Link to="/splits" className="small" style={{ display: 'inline-flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
        <ArrowLeft size={14} /> All groups
      </Link>
      <PageHeader title={group.name} subtitle={group.description || `Created by ${userName(group.owner, me)}`}>
        <button className="btn" onClick={() => setModal({ type: 'bill' })}>
          <Plus size={16} /> Add bill
        </button>
        <button className="btn secondary" onClick={() => setModal({ type: 'settle' })}>
          <HandCoins size={16} /> Record payment
        </button>
        {isAdmin && (
          <button className="btn ghost" title="Edit group" onClick={() => setModal({ type: 'edit' })}>
            <Pencil size={16} />
          </button>
        )}
        {isOwner ? (
          <button
            className="btn ghost"
            style={{ color: 'var(--danger)' }}
            title={group.settled ? 'Delete group' : 'Everyone must settle up before the group can be deleted'}
            onClick={deleteGroup}
          >
            <Trash2 size={16} />
          </button>
        ) : (
          <button className="btn ghost" title="Leave group" onClick={() => setModal({ type: 'remove', member: mine })}>
            <LogOut size={16} />
          </button>
        )}
      </PageHeader>
      <Msg msg={msg} />

      <div className="tiles">
        <StatTile
          hero
          negative={myNet < -0.009}
          icon={Scale}
          label="Your balance"
          value={money(Math.abs(myNet))}
          sub={Math.abs(myNet) < 0.01 ? 'Settled up' : myNet > 0 ? 'You get back' : 'You owe'}
        />
        <StatTile icon={Receipt} label="Group spending" value={money(total)} sub={`${group.bills.length} bills`} />
        <StatTile icon={Wallet} label="You paid" value={money(mine?.paid)} sub={`Your share ${money(mine?.share)}`} />
        <StatTile icon={HandCoins} label="Paid back" value={money(mine?.sent)} sub={`Received ${money(mine?.received)}`} />
      </div>

      <div className="segmented" style={{ marginBottom: 16 }}>
        {[
          ['bills', 'Bills', Receipt],
          ['balances', 'Balances', Scale],
          ['members', 'Members', Users],
          ['payments', 'Payments', HandCoins],
        ].map(([k, l, Icon]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
            <Icon size={14} /> {l}
          </button>
        ))}
      </div>

      {tab === 'bills' && (
        <div className="card flush">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Bill</th>
                  <th>Paid by</th>
                  <th className="num">Total</th>
                  <th className="num">Your share</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {group.bills.map((b) => {
                  const my = b.shares.find((s) => s.member.id === myId)
                  return (
                    <tr key={b.id}>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}>{b.bill_date}</td>
                      <td>
                        <b>{b.title}</b>
                        {b.note && <div className="small muted">{b.note}</div>}
                        <div className="bill-shares">
                          {b.shares.map((s) => (
                            <span key={s.member.id} className="badge" title={`${SHARE_LABEL[s.share_type]} share`}>
                              {name(s.member, me)} {money(s.amount)} <span className="muted">· {shareText(s)}</span>
                            </span>
                          ))}
                        </div>
                        <div className="small muted" style={{ marginTop: 4 }}>
                          Added by {userName(b.created_by, me)}
                          {b.updated_at ? ' · edited' : ''}
                        </div>
                      </td>
                      <td>{name(b.paid_by, me)}</td>
                      <td className="num strong">{money(b.total_amount)}</td>
                      <td className="num">{my ? money(my.amount) : <span className="muted">—</span>}</td>
                      <td>
                        {b.can_edit && (
                          <div className="actions" style={{ flexWrap: 'nowrap' }}>
                            <button className="btn ghost small" title="Edit" onClick={() => setModal({ type: 'bill', bill: b })}>
                              <Pencil size={14} />
                            </button>
                            <button
                              className="btn ghost small"
                              title="Delete"
                              style={{ color: 'var(--danger)' }}
                              onClick={() =>
                                window.confirm(`Delete the bill “${b.title}”?`) &&
                                act(() => api.delete(`/splits/bills/${b.id}`), 'Bill deleted. Everyone was notified.')
                              }
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {!group.bills.length && <EmptyRow cols={6}>No bills yet. Add the first one.</EmptyRow>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'balances' && (
        <div className="grid-2">
          <div className="card">
            <div className="card-head">
              <div>
                <h3>Who owes whom</h3>
                <p>After everything paid back so far.</p>
              </div>
            </div>
            <ul className="list">
              {group.debts.map((d) => (
                <li key={`${d.from.id}-${d.to.id}`}>
                  <div className="debt">
                    <Avatar user={avatarOf(d.from)} size={28} />
                    <b>{name(d.from, me)}</b>
                    <ArrowRight size={14} className="muted" />
                    <Avatar user={avatarOf(d.to)} size={28} />
                    <b>{name(d.to, me)}</b>
                  </div>
                  <div className="actions">
                    <span className="strong">{money(d.amount)}</span>
                    {canSettle(d) && (
                      <button className="btn secondary small" onClick={() => setModal({ type: 'settle', debt: d })}>
                        Settle
                      </button>
                    )}
                  </div>
                </li>
              ))}
              {!group.debts.length && <li className="muted">Everyone is settled up.</li>}
            </ul>
          </div>
          <div className="card flush">
            <div className="card-head">
              <h3>Per person</h3>
            </div>
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Person</th>
                    <th className="num">Paid bills</th>
                    <th className="num">Share</th>
                    <th className="num">Paid back</th>
                    <th className="num">Received</th>
                    <th className="num">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {group.members.map((m) => {
                    const net = Number(m.net)
                    return (
                      <tr key={m.member.id} className={m.active ? '' : 'dim'}>
                        <td className="strong">
                          {name(m.member, me)}
                          {m.member.is_guest && <span className="muted small"> (guest)</span>}
                          {!m.active && <span className="muted small"> (left)</span>}
                        </td>
                        <td className="num">{money(m.paid)}</td>
                        <td className="num">{money(m.share)}</td>
                        <td className="num">{money(m.sent)}</td>
                        <td className="num">{money(m.received)}</td>
                        <td className={`num ${net > 0.009 ? 'net-pos' : net < -0.009 ? 'net-neg' : 'muted'}`}>
                          {Math.abs(net) < 0.01 ? 'settled' : `${net > 0 ? '+' : '−'}${money(Math.abs(net))}`}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'members' && (
        <div className="card">
          <div className="card-head">
            <div>
              <h3>Members</h3>
              <p>
                Group admins can add and remove members and make others admin. Only the owner can delete the group. Guests aren’t on the app:
                they’re tracked by name and don’t get notifications.
              </p>
            </div>
            {isAdmin && (
              <button className="btn small" onClick={() => setModal({ type: 'add' })}>
                <UserPlus size={14} /> Add member
              </button>
            )}
          </div>
          <ul className="list">
            {group.members
              .filter((m) => m.active)
              .map((m) => (
                <li key={m.member.id}>
                  <div className="cell-user">
                    <Avatar user={avatarOf(m.member)} size={32} />
                    <div>
                      <b>{name(m.member, me)}</b>
                      <small>{m.member.is_guest ? 'Guest · not on the app' : handle(m.member)}</small>
                    </div>
                    {m.is_owner ? (
                      <span className="badge info">
                        <Crown size={11} /> Owner
                      </span>
                    ) : (
                      m.is_admin && <span className="badge info">Admin</span>
                    )}
                  </div>
                  {isAdmin && !m.is_owner && (
                    <div className="actions">
                      {!m.member.is_guest && (
                        <button
                          className="btn ghost small"
                          title={m.is_admin ? 'Remove admin rights' : 'Make admin'}
                          onClick={() =>
                            act(
                              () => api.patch(`/splits/groups/${group.id}/members/${m.member.id}`, { is_admin: !m.is_admin }),
                              m.is_admin ? `${m.member.full_name} is no longer an admin` : `${m.member.full_name} is now an admin`,
                            )
                          }
                        >
                          {m.is_admin ? <ShieldMinus size={14} /> : <ShieldPlus size={14} />}
                        </button>
                      )}
                      {m.member.id !== myId && (
                        <button className="btn danger small" onClick={() => setModal({ type: 'remove', member: m })}>
                          <UserMinus size={14} /> Remove
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}

      {tab === 'payments' && (
        <div className="card flush">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>From</th>
                  <th>To</th>
                  <th className="num">Amount</th>
                  <th>Note</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {group.settlements.map((s) => (
                  <tr key={s.id}>
                    <td className="muted">{s.settled_on}</td>
                    <td className="strong">{name(s.from, me)}</td>
                    <td className="strong">{name(s.to, me)}</td>
                    <td className="num strong">{money(s.amount)}</td>
                    <td className="muted">{s.note}</td>
                    <td>
                      {s.can_edit && (
                        <div className="actions">
                          <button
                            className="btn ghost small"
                            title="Delete"
                            style={{ color: 'var(--danger)' }}
                            onClick={() =>
                              window.confirm(`Delete this payment of ${money(s.amount)}?`) &&
                              act(() => api.delete(`/splits/settlements/${s.id}`), 'Payment deleted')
                            }
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {!group.settlements.length && <EmptyRow cols={6}>No payments recorded yet.</EmptyRow>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {modal?.type === 'bill' && <BillForm group={group} bill={modal.bill} onClose={() => setModal(null)} onSaved={saved} />}
      {modal?.type === 'settle' && <SettleForm group={group} me={me} debt={modal.debt} onClose={() => setModal(null)} onSaved={saved} />}
      {modal?.type === 'edit' && <EditGroup group={group} onClose={() => setModal(null)} onSaved={saved} />}
      {modal?.type === 'add' && <AddMember group={group} me={me} onClose={() => setModal(null)} onDone={load} />}
      {modal?.type === 'remove' && (
        <RemoveMember
          group={group}
          member={modal.member}
          me={me}
          onClose={() => {
            const left = modal.left
            setModal(null)
            if (left) navigate('/splits')
          }}
          onDone={(self) => (self ? setModal({ ...modal, left: true }) : load())}
        />
      )}
    </div>
  )
}
