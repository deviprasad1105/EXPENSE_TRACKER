import { useCallback, useEffect, useState } from 'react'
import { Eye, Share2, UserMinus, Users } from 'lucide-react'
import api, { errorMessage } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import { Msg, PageHeader } from '../components/ui'

function Person({ u, children }) {
  return (
    <div className="cell-user">
      <Avatar user={u} size={32} />
      <div>
        <b>{u.full_name}</b>
        <small>@{u.username}</small>
      </div>
      {children}
    </div>
  )
}

function TeamTree({ nodes, onView }) {
  return (
    <ul className="tree">
      {nodes.map((n) => (
        <li key={n.id}>
          <div className="tree-node">
            <Person u={n} />
            <button className="btn secondary small" onClick={() => onView(n.id)}>
              <Eye size={14} /> View
            </button>
          </div>
          {n.reports.length > 0 && <TeamTree nodes={n.reports} onView={onView} />}
        </li>
      ))}
    </ul>
  )
}

const countTeam = (nodes) => nodes.reduce((s, n) => s + 1 + countTeam(n.reports), 0)

export default function Sharing() {
  const { user, refresh, setViewUserId } = useAuth()
  const [data, setData] = useState(null)
  const [username, setUsername] = useState('')
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => setData((await api.get('/shares')).data), [])
  useEffect(() => {
    load()
  }, [load])

  const share = async (e) => {
    e.preventDefault()
    try {
      await api.post('/shares', { viewer_username: username.trim() })
      setUsername('')
      setMsg({ type: 'ok', text: 'Shared. They can now view your data, but not change it.' })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const revoke = async (s, label) => {
    if (!window.confirm(label)) return
    await api.delete(`/shares/${s.id}`)
    load()
    refresh()
  }

  if (!data) return <div className="muted">Loading…</div>
  const teamSize = countTeam(data.team)

  return (
    <div>
      <PageHeader title="Team & Sharing" subtitle="Your reporting line, and who can see your data." />
      <Msg msg={msg} />

      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <div>
              <h3>My team</h3>
              <p>
                {teamSize
                  ? `${teamSize} people report to you, directly or through others. You can view their expenses but not change them.`
                  : 'Nobody reports to you yet. People choose you as their manager when they register, or an admin sets it.'}
              </p>
            </div>
            <Users size={20} className="muted" />
          </div>
          {teamSize > 0 && (
            <>
              <div className="tree-node">
                <Person u={user} />
                <span className="badge info">You</span>
              </div>
              <TeamTree nodes={data.team} onView={setViewUserId} />
            </>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Reporting line</h3>
              <p>These people can view your expenses because you report to them, directly or through others.</p>
            </div>
          </div>
          {data.managers.length ? (
            <ul className="list">
              {data.managers.map((m, i) => (
                <li key={m.id}>
                  <Person u={m} />
                  <span className="badge">{i === 0 ? 'Direct manager' : `Level ${i + 1}`}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">You don’t report to anyone.</p>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Share my data</h3>
              <p>The person you share with can view your expenses, balance and reports, but cannot change anything.</p>
            </div>
            <Share2 size={20} className="muted" />
          </div>
          <form className="filters" onSubmit={share} style={{ marginBottom: 8 }}>
            <input placeholder="Their username" value={username} onChange={(e) => setUsername(e.target.value)} required style={{ flex: 1 }} />
            <button className="btn">Share</button>
          </form>
          <ul className="list">
            {data.shared_by_me.map((s) => (
              <li key={s.id}>
                <Person u={s.viewer} />
                <button className="btn danger small" onClick={() => revoke(s, `Stop sharing with ${s.viewer.username}?`)}>
                  <UserMinus size={14} /> Revoke
                </button>
              </li>
            ))}
            {!data.shared_by_me.length && <li className="muted">You haven’t shared your data with anyone.</li>}
          </ul>
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Shared with me</h3>
              <p>View only.</p>
            </div>
          </div>
          <ul className="list">
            {data.shared_with_me.map((s) => (
              <li key={s.id}>
                <Person u={s.owner} />
                <div className="actions">
                  <button className="btn secondary small" onClick={() => setViewUserId(s.owner.id)}>
                    <Eye size={14} /> View
                  </button>
                  <button className="btn ghost small" onClick={() => revoke(s, `Remove ${s.owner.username}'s shared data from your list?`)}>
                    Remove
                  </button>
                </div>
              </li>
            ))}
            {!data.shared_with_me.length && <li className="muted">Nobody has shared their data with you.</li>}
          </ul>
        </div>
      </div>
    </div>
  )
}
