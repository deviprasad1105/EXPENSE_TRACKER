import { useCallback, useEffect, useState } from 'react'
import { ArchiveRestore, AtSign, Check, DatabaseBackup, Download, Eraser, Eye, Lock, Search, Trash2, Unlock, Users, X } from 'lucide-react'
import api, { download, errorMessage } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import { Alert, EmptyRow, Msg, PageHeader } from '../components/ui'

const STATUS_CLS = { APPROVED: 'ok', PENDING: 'warn', REJECTED: 'bad' }
const KIND_LABEL = { USER: 'Account + data', USER_DATA: 'All data', EXPENSE: 'Expense', FUND: 'Fund', DAILY_ITEM: 'Daily item' }
const size = (b) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)
const fmt = (d) => (d ? new Date(d).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '')

export default function Admin() {
  const { user: me, refresh, setViewUserId } = useAuth()
  const [tab, setTab] = useState('users')
  const [users, setUsers] = useState([])
  const [requests, setRequests] = useState([])
  const [backups, setBackups] = useState([])
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [u, r, b] = await Promise.all([api.get('/admin/users'), api.get('/admin/username-requests'), api.get('/admin/backups')])
    setUsers(u.data)
    setRequests(r.data)
    setBackups(b.data)
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
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    }
  }

  const lock = (u) => {
    const reason = window.prompt(`Lock @${u.username}? They will be signed out and cannot sign in until unlocked.\n\nReason (shown to them on sign-in, optional):`, '')
    if (reason === null) return
    act(() => api.post(`/admin/users/${u.id}/lock`, { reason: reason || null }), `@${u.username} locked`)
  }

  const clearData = (u) => {
    const reason = window.prompt(
      `Delete ALL expenses, funds and daily items of @${u.username}? The account stays.\n\nA backup is saved first, and you can restore it from the Backups tab.\n\nReason (optional):`,
      '',
    )
    if (reason === null) return
    act(() => api.post(`/admin/users/${u.id}/clear-data`, { reason: reason || null }), `Data of @${u.username} deleted. Backup saved.`)
  }

  const removeUser = (u) => {
    const reason = window.prompt(
      `Permanently delete @${u.username} and ALL their data?\n\nA full backup is saved first, and you can restore it from the Backups tab.\n\nReason (optional):`,
      '',
    )
    if (reason === null) return
    act(() => api.delete(`/admin/users/${u.id}`, { params: reason ? { reason } : {} }), `@${u.username} deleted. Backup saved.`)
  }

  const restore = (b) => {
    if (!window.confirm(`Restore backup #${b.id} (${b.summary})?`)) return
    act(async () => {
      const res = await api.post(`/admin/backups/${b.id}/restore`)
      setMsg({ type: 'ok', text: res.data.message })
    }, `Backup #${b.id} restored`)
  }

  const discard = (b) =>
    window.confirm(`Permanently discard backup #${b.id}? This data can then never be restored.`) &&
    act(() => api.delete(`/admin/backups/${b.id}`), `Backup #${b.id} discarded`)

  const decide = (r, approve) => {
    const comment = window.prompt(
      approve ? `Change @${r.current_username} to @${r.requested_username}? Optional note for the user:` : 'Reason for rejecting (optional):',
      '',
    )
    if (comment === null) return
    act(
      () => api.post(`/admin/username-requests/${r.id}/${approve ? 'approve' : 'reject'}`, { comment: comment || null }),
      approve ? `Username changed to @${r.requested_username}` : 'Request rejected',
    )
  }

  const byId = Object.fromEntries(users.map((u) => [u.id, u]))
  const orgAdmins = users.filter((u) => u.role === 'ORG_ADMIN')
  const pendingUsers = users.filter((u) => u.status === 'PENDING').length
  const pendingReqs = requests.filter((r) => r.status === 'PENDING').length
  const q = search.trim().toLowerCase()
  const shown = users.filter(
    (u) => (!filter || u.status === filter) && (!q || `${u.full_name} ${u.username} ${u.email}`.toLowerCase().includes(q)),
  )

  return (
    <div>
      <PageHeader title="Administration" subtitle="Approve and lock accounts, choose each user’s role and plan, set reporting lines and organisations, handle username requests and restore deleted data. Locking an organisation admin locks everyone in their organisation.">
        <div className="segmented">
          <button className={tab === 'users' ? 'on' : ''} onClick={() => setTab('users')}>
            <Users size={14} /> Users {pendingUsers > 0 && <span className="pill">{pendingUsers}</span>}
          </button>
          <button className={tab === 'requests' ? 'on' : ''} onClick={() => setTab('requests')}>
            <AtSign size={14} /> Username requests {pendingReqs > 0 && <span className="pill">{pendingReqs}</span>}
          </button>
          <button className={tab === 'backups' ? 'on' : ''} onClick={() => setTab('backups')}>
            <DatabaseBackup size={14} /> Backups
          </button>
        </div>
      </PageHeader>
      <Msg msg={msg} />

      {tab === 'users' && (
        <>
          {pendingUsers > 0 && <Alert type="warn">{pendingUsers} user(s) are waiting for approval.</Alert>}
          <div className="card flush">
            <div className="toolbar">
              <div style={{ position: 'relative' }}>
                <Search size={16} className="muted" style={{ position: 'absolute', left: 10, top: 11 }} />
                <input placeholder="Search users" value={search} onChange={(e) => setSearch(e.target.value)} style={{ paddingLeft: 32 }} />
              </div>
              <div className="segmented">
                {[['', 'All'], ['PENDING', 'Pending'], ['APPROVED', 'Approved'], ['REJECTED', 'Rejected']].map(([v, l]) => (
                  <button key={v} className={filter === v ? 'on' : ''} onClick={() => setFilter(v)}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Status</th>
                    <th>Role</th>
                    <th>Plan</th>
                    <th>Reports to</th>
                    <th>Organisation</th>
                    <th>Registered</th>
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
                        <span className={`badge ${STATUS_CLS[u.status]}`}>{u.status.toLowerCase()}</span>{' '}
                        {u.is_locked && (
                          <span className="badge bad" title={`Locked by ${u.locked_by} on ${fmt(u.locked_at)}${u.lock_reason ? ` · ${u.lock_reason}` : ''}`}>
                            <Lock size={11} /> locked
                          </span>
                        )}
                        {u.org_locked && !u.is_locked && (
                          <span className="badge bad" title={`Their organisation admin @${byId[u.org_admin_id]?.username} is locked`}>
                            <Lock size={11} /> org locked
                          </span>
                        )}
                      </td>
                      <td>
                        <select
                          value={u.role}
                          disabled={u.id === me.id}
                          onChange={(e) => act(() => api.patch(`/admin/users/${u.id}`, { role: e.target.value }), 'Role updated')}
                        >
                          <option value="USER">User</option>
                          <option value="ORG_ADMIN">Org admin</option>
                          <option value="ADMIN">Admin</option>
                        </select>
                      </td>
                      <td>
                        {u.role === 'ADMIN' ? (
                          <span className="muted small">All features</span>
                        ) : (
                          <div className="stack" style={{ gap: 4 }}>
                            {[
                              ['feature_expense', 'Expenses'],
                              ['feature_split', 'Split bills'],
                              ['feature_subscription', 'Subscriptions'],
                              ['feature_accounting', 'Accounting'],
                            ].map(([key, label]) => (
                              <label key={key} className="check" style={{ whiteSpace: 'nowrap' }}>
                                <input
                                  type="checkbox"
                                  checked={u[key]}
                                  onChange={(e) =>
                                    act(
                                      () => api.patch(`/admin/users/${u.id}`, { [key]: e.target.checked }),
                                      `${label} ${e.target.checked ? 'enabled' : 'disabled'} for @${u.username}`,
                                    )
                                  }
                                />
                                {label}
                                {key === 'feature_accounting' && u.feature_accounting && !u.feature_expense && (
                                  <span className="muted small">(needs Expenses)</span>
                                )}
                              </label>
                            ))}
                          </div>
                        )}
                      </td>
                      <td>
                        {u.role === 'ADMIN' ? (
                          <span className="muted small">Nobody (admin)</span>
                        ) : (
                        <select
                          value={u.manager_id || ''}
                          onChange={(e) =>
                            act(
                              () => api.patch(`/admin/users/${u.id}`, e.target.value ? { manager_id: Number(e.target.value) } : { clear_manager: true }),
                              'Reporting line updated',
                            )
                          }
                        >
                          <option value="">No manager</option>
                          {users
                            .filter((m) => m.id !== u.id && m.status === 'APPROVED')
                            .map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.full_name} (@{m.username})
                              </option>
                            ))}
                        </select>
                        )}
                        {u.manager_id && !byId[u.manager_id] && <span className="muted"> #{u.manager_id}</span>}
                      </td>
                      <td>
                        {u.role === 'ORG_ADMIN' ? (
                          <span className="badge info">Heads organisation</span>
                        ) : u.role === 'ADMIN' ? (
                          <span className="muted small">—</span>
                        ) : (
                          <select
                            value={u.org_admin_id || ''}
                            onChange={(e) =>
                              act(
                                () => api.patch(`/admin/users/${u.id}`, e.target.value ? { org_admin_id: Number(e.target.value) } : { clear_org: true }),
                                'Organisation updated',
                              )
                            }
                          >
                            <option value="">None</option>
                            {orgAdmins.map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.full_name} (@{o.username})
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}>{u.created_at.slice(0, 10)}</td>
                      <td>
                        <div className="actions">
                          {u.status !== 'APPROVED' && (
                            <button className="btn small" onClick={() => act(() => api.post(`/admin/users/${u.id}/approve`), `@${u.username} approved`)}>
                              <Check size={14} /> Approve
                            </button>
                          )}
                          {u.status === 'PENDING' && (
                            <button className="btn secondary small" onClick={() => act(() => api.post(`/admin/users/${u.id}/reject`), `@${u.username} rejected`)}>
                              <X size={14} /> Reject
                            </button>
                          )}
                          {u.status === 'APPROVED' && (
                            <button className="btn ghost small" title="View their data" onClick={() => setViewUserId(u.id)}>
                              <Eye size={14} />
                            </button>
                          )}
                          {u.id !== me.id &&
                            (u.is_locked ? (
                              <button className="btn ghost small" title="Unlock account" onClick={() => act(() => api.post(`/admin/users/${u.id}/unlock`), `@${u.username} unlocked`)}>
                                <Unlock size={14} />
                              </button>
                            ) : (
                              <button className="btn ghost small" title="Lock account" onClick={() => lock(u)}>
                                <Lock size={14} />
                              </button>
                            ))}
                          <button className="btn ghost small" title="Delete all their data (backed up)" onClick={() => clearData(u)} style={{ color: 'var(--warning)' }}>
                            <Eraser size={14} />
                          </button>
                          {u.id !== me.id && (
                            <button className="btn ghost small" title="Delete user (backed up)" style={{ color: 'var(--danger)' }} onClick={() => removeUser(u)}>
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!shown.length && <EmptyRow cols={8}>No users match.</EmptyRow>}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {tab === 'backups' && (
        <div className="card flush">
          <div className="card-head">
            <div>
              <h3>Backups of deleted data</h3>
              <p>
                Every time an admin deletes a user, a user’s data, or a single expense, fund or daily item, a full copy is saved here first.
                Restoring puts the rows back with their original IDs.
              </p>
            </div>
          </div>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Deleted</th>
                  <th>What</th>
                  <th className="num">Rows</th>
                  <th>By</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {backups.map((b) => (
                  <tr key={b.id}>
                    <td className="muted">{b.id}</td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(b.created_at)}</td>
                    <td>
                      <span className="badge info">{KIND_LABEL[b.kind] || b.kind}</span> <b>@{b.username}</b>
                      <div className="small muted">{b.summary}</div>
                      {b.reason && <div className="small muted">Reason: {b.reason}</div>}
                    </td>
                    <td className="num">
                      {b.row_count}
                      <div className="small muted">{size(b.size_bytes)}</div>
                    </td>
                    <td>@{b.created_by}</td>
                    <td>
                      {b.restored_at ? (
                        <span className="badge ok" title={`by @${b.restored_by}`}>
                          restored {fmt(b.restored_at)}
                        </span>
                      ) : (
                        <span className="badge">available</span>
                      )}
                    </td>
                    <td>
                      <div className="actions" style={{ flexWrap: 'nowrap' }}>
                        <button className="btn ghost small" title="Download dump (.json)" onClick={() => download(`/admin/backups/${b.id}/download`, {}, `backup_${b.id}.json`)}>
                          <Download size={14} />
                        </button>
                        {!b.restored_at && (
                          <button className="btn secondary small" onClick={() => restore(b)}>
                            <ArchiveRestore size={14} /> Restore
                          </button>
                        )}
                        <button className="btn ghost small" title="Discard backup permanently" style={{ color: 'var(--danger)' }} onClick={() => discard(b)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!backups.length && <EmptyRow cols={7}>No backups yet. They’re created automatically whenever an admin deletes data.</EmptyRow>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'requests' && (
        <div className="card flush">
          <div className="card-head">
            <div>
              <h3>Username change requests</h3>
              <p>Users can change their own username once every 15 days. Inside that window they ask here. Approving restarts their 15 days.</p>
            </div>
          </div>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Change</th>
                  <th>Reason</th>
                  <th>Status</th>
                  <th>Requested</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id}>
                    <td className="strong">{r.full_name}</td>
                    <td>
                      @{r.current_username} → <b>@{r.requested_username}</b>
                    </td>
                    <td className="muted">{r.reason}</td>
                    <td>
                      <span className={`badge ${STATUS_CLS[r.status]}`}>{r.status.toLowerCase()}</span>
                      {r.admin_comment && <div className="small muted">{r.admin_comment}</div>}
                    </td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(r.created_at)}</td>
                    <td>
                      {r.status === 'PENDING' && (
                        <div className="actions">
                          <button className="btn small" onClick={() => decide(r, true)}>
                            <Check size={14} /> Approve
                          </button>
                          <button className="btn secondary small" onClick={() => decide(r, false)}>
                            <X size={14} /> Reject
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {!requests.length && <EmptyRow cols={6}>No username requests.</EmptyRow>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
