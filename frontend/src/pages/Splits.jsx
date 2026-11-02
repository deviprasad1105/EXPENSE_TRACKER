import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowDownCircle, ArrowUpCircle, Crown, Plus, Receipt, SplitSquareHorizontal, Users } from 'lucide-react'
import api, { errorMessage, money } from '../api'
import { hasFeature, useAuth } from '../auth'
import Modal from '../components/Modal'
import { Msg, PageHeader, StatTile } from '../components/ui'

export function NetLabel({ net }) {
  const v = Number(net)
  if (Math.abs(v) < 0.01) return <span className="muted">Settled up</span>
  return v > 0 ? <span className="net-pos">You get back {money(v)}</span> : <span className="net-neg">You owe {money(-v)}</span>
}

function CreateGroup({ onClose }) {
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', description: '', members: '' })
  const [msg, setMsg] = useState(null)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    try {
      const res = await api.post('/splits/groups', {
        name: form.name.trim(),
        description: form.description || null,
        members: form.members.split(',').map((m) => m.trim()).filter(Boolean),
      })
      navigate(`/splits/${res.data.id}`)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  return (
    <Modal title="New split group" subtitle="You’ll be the group’s owner and admin." onClose={onClose}>
      <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
        <Msg msg={msg} />
        <div className="field">
          <label>Group name</label>
          <input value={form.name} onChange={set('name')} placeholder="Goa trip, Flat 302, Office lunch…" maxLength={100} required autoFocus />
        </div>
        <div className="field">
          <label>Description</label>
          <input value={form.description} onChange={set('description')} maxLength={500} />
        </div>
        <div className="field">
          <label>Members</label>
          <input value={form.members} onChange={set('members')} placeholder="@asha, @vikram, Ravi Kumar" />
          <span className="hint">
            Separate with commas. Use @username for people on the app; they see the group and get notified. A plain name adds a guest who
            isn’t on the app, tracked only in this group. You can add more later.
          </span>
        </div>
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn">
            <Plus size={16} /> Create group
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default function Splits() {
  const { user } = useAuth()
  const canCreate = hasFeature(user, 'split')
  const [groups, setGroups] = useState(null)
  const [creating, setCreating] = useState(false)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => setGroups((await api.get('/splits/groups')).data), [])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  if (!groups) return <div className="muted">{msg ? <Msg msg={msg} /> : 'Loading…'}</div>
  const owed = groups.reduce((s, g) => s + Math.max(Number(g.my_net), 0), 0)
  const owe = groups.reduce((s, g) => s + Math.max(-Number(g.my_net), 0), 0)

  return (
    <div>
      <PageHeader title="Split Bills" subtitle="Share bills with friends, see who owes whom, and settle up.">
        {canCreate && (
          <button className="btn" onClick={() => setCreating(true)}>
            <Plus size={16} /> New group
          </button>
        )}
      </PageHeader>
      <Msg msg={msg} />
      <div className="tiles">
        <StatTile hero negative={owe > owed} icon={SplitSquareHorizontal} label="Overall" value={money(Math.abs(owed - owe))} sub={owed - owe >= 0 ? 'You get back' : 'You owe'} />
        <StatTile icon={ArrowDownCircle} label="You are owed" value={money(owed)} />
        <StatTile icon={ArrowUpCircle} label="You owe" value={money(owe)} />
      </div>

      <div className="group-grid">
        {groups.map((g) => (
          <Link key={g.id} to={`/splits/${g.id}`} className="card group-card">
            <div className="row-between">
              <h3>{g.name}</h3>
              {g.is_owner ? (
                <span className="badge info">
                  <Crown size={11} /> Owner
                </span>
              ) : (
                g.is_admin && <span className="badge info">Admin</span>
              )}
            </div>
            {g.description && <div className="muted small">{g.description}</div>}
            <NetLabel net={g.my_net} />
            <div className="meta">
              <span>
                <Users size={12} /> {g.member_count} members{g.guest_count ? ` · ${g.guest_count} guests` : ''}
              </span>
              <span>
                <Receipt size={12} /> {g.bill_count} bills · {money(g.total_spent)}
              </span>
            </div>
          </Link>
        ))}
      </div>
      {!groups.length && (
        <div className="card empty">
          <SplitSquareHorizontal size={28} />
          {canCreate
            ? 'No groups yet. Create one to start splitting bills.'
            : 'You’re not in any split group. Someone can add you to theirs, or ask an administrator for the split bills plan to create your own.'}
        </div>
      )}
      {creating && <CreateGroup onClose={() => setCreating(false)} />}
    </div>
  )
}
