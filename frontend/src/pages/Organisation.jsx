import { useCallback, useEffect, useState } from 'react'
import { Eye, Lock, Search, Trash2, Unlock, UserPlus } from 'lucide-react'
import api, { errorMessage } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import { EmptyRow, Msg, PageHeader } from '../components/ui'

const fmt = (d) => (d ? new Date(d).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '')
const EMPTY = { full_name: '', username: '', email: '', password: '' }

/** The organisation admin's console: add, view, lock, unlock and remove the members of their organisation. */
export default function Organisation() {
  const { refresh, setViewUserId } = useAuth()
  const [members, setMembers] = useState([])
  const [search, setSearch] = useState('')
  const [form, setForm] = useState(EMPTY)
  const [adding, setAdding] = useState(false)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const res = await api.get('/org/members')
    setMembers(res.data)
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const act = async (fn, okText) => {
    try {
      await fn()
      setMsg({ type: 'ok', text: okText })
      await load()
      refresh()
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }

  const add = async (e) => {
    e.preventDefault()
    setAdding(true)
    if (await act(() => api.post('/org/members', form), `@${form.username} added to your organisation`)) setForm(EMPTY)
    setAdding(false)
  }

  const lock = (u) => {
    const reason = window.prompt(`Lock @${u.username}? They will be signed out and cannot sign in until unlocked.\n\nReason (shown to them on sign-in, optional):`, '')
    if (reason === null) return
    act(() => api.post(`/org/members/${u.id}/lock`, { reason: reason || null }), `@${u.username} locked`)
  }

  const remove = (u) => {
    const reason = window.prompt(`Permanently remove @${u.username} and ALL their data from your organisation?\n\nReason (optional):`, '')
    if (reason === null) return
    act(() => api.delete(`/org/members/${u.id}`, { params: reason ? { reason } : {} }), `@${u.username} removed`)
  }

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const q = search.trim().toLowerCase()
  const shown = members.filter((u) => !q || `${u.full_name} ${u.username} ${u.email}`.toLowerCase().includes(q))

  return (
    <div>
      <PageHeader title="Organisation" subtitle="Add people to your organisation, view their data, and lock, unlock or remove their accounts." />
      <Msg msg={msg} />

      <form className="card" onSubmit={add} style={{ marginBottom: 16 }}>
        <div className="card-head">
          <div>
            <h3>Add a member</h3>
            <p>The account is ready to use straight away, with the same plan as yours. Share the password with them.</p>
          </div>
        </div>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <div>
            <label>Full name</label>
            <input required maxLength={120} value={form.full_name} onChange={set('full_name')} />
          </div>
          <div>
            <label>Username</label>
            <input required minLength={3} maxLength={50} pattern="[A-Za-z0-9_.\-]+" value={form.username} onChange={set('username')} />
          </div>
          <div>
            <label>Email</label>
            <input required type="email" maxLength={120} value={form.email} onChange={set('email')} />
          </div>
          <div>
            <label>Password</label>
            <input required type="password" minLength={8} maxLength={100} autoComplete="new-password" value={form.password} onChange={set('password')} />
          </div>
          <div className="form-actions span-all">
            <button className="btn" disabled={adding}>
              <UserPlus size={14} /> Add member
            </button>
          </div>
        </div>
      </form>

      <div className="card flush">
        <div className="toolbar">
          <div style={{ position: 'relative' }}>
            <Search size={16} className="muted" style={{ position: 'absolute', left: 10, top: 11 }} />
            <input placeholder="Search members" value={search} onChange={(e) => setSearch(e.target.value)} style={{ paddingLeft: 32 }} />
          </div>
          <span className="muted small">{members.length} member(s)</span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Member</th>
                <th>Status</th>
                <th>Added</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="cell-user">
                      <Avatar user={u} size={34} />
                      <div>
                        <b>{u.full_name}</b>
                        <small>
                          @{u.username} · {u.email}
                        </small>
                      </div>
                    </div>
                  </td>
                  <td>
                    {u.is_locked ? (
                      <span className="badge bad" title={`Locked by ${u.locked_by} on ${fmt(u.locked_at)}${u.lock_reason ? ` · ${u.lock_reason}` : ''}`}>
                        <Lock size={11} /> locked
                      </span>
                    ) : (
                      <span className="badge ok">active</span>
                    )}
                  </td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{u.created_at.slice(0, 10)}</td>
                  <td>
                    <div className="actions">
                      {u.status === 'APPROVED' && (
                        <button className="btn ghost small" title="View their data (they are not notified)" onClick={() => setViewUserId(u.id)}>
                          <Eye size={14} />
                        </button>
                      )}
                      {u.is_locked ? (
                        <button className="btn ghost small" title="Unlock account" onClick={() => act(() => api.post(`/org/members/${u.id}/unlock`), `@${u.username} unlocked`)}>
                          <Unlock size={14} />
                        </button>
                      ) : (
                        <button className="btn ghost small" title="Lock account" onClick={() => lock(u)}>
                          <Lock size={14} />
                        </button>
                      )}
                      <button className="btn ghost small" title="Remove from organisation" style={{ color: 'var(--danger)' }} onClick={() => remove(u)}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!shown.length && <EmptyRow cols={4}>{members.length ? 'No members match.' : 'No members yet. Add the first one above.'}</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
