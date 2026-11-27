import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArchiveRestore, AtSign, Check, History, MonitorSmartphone, DatabaseBackup, Download, Eraser, Eye, Lock, MailWarning, Search, Settings2, ShieldCheck, ShieldOff, Trash2, Unlock, UserX, Users, X } from 'lucide-react'
import api, { download, errorMessage } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import { Alert, EmptyRow, Msg, PageHeader } from '../components/ui'

const STATUS_CLS = { APPROVED: 'ok', PENDING: 'warn', REJECTED: 'bad' }
const KIND_LABEL = { USER: 'Account + data', USER_DATA: 'All data', PURGE: 'Purged date range', EXPENSE: 'Expense', FUND: 'Fund', DAILY_ITEM: 'Daily item' }
const size = (b) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)
const fmt = (d) => (d ? new Date(d).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '')

export default function Admin() {
  const { user: me, refresh, setViewUserId } = useAuth()
  const [params] = useSearchParams()
  // ?tab=deletions opens a tab directly (the "Review" link of a deletion request notification)
  const [tab, setTab] = useState(params.get('tab') || 'users')
  const [users, setUsers] = useState([])
  const [requests, setRequests] = useState([])
  const [deletions, setDeletions] = useState([])
  const [backups, setBackups] = useState([])
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [msg, setMsg] = useState(null)
  const [otpCfg, setOtpCfg] = useState(null)
  const [secCfg, setSecCfg] = useState(null)
  // Sign-ins tab: whose (null = everyone), their devices and the log
  const [loginUser, setLoginUser] = useState(null)
  const [logins, setLogins] = useState([])
  const [devices, setDevices] = useState([])
  const [failedOnly, setFailedOnly] = useState(false)

  const load = useCallback(async () => {
    const [u, r, b, d, o, sec] = await Promise.all([
      api.get('/admin/users'),
      api.get('/admin/username-requests'),
      api.get('/admin/backups'),
      api.get('/admin/deletion-requests'),
      api.get('/admin/settings/otp'),
      api.get('/admin/settings/security'),
    ])
    setUsers(u.data)
    setRequests(r.data)
    setBackups(b.data)
    setDeletions(d.data)
    setOtpCfg(o.data)
    setSecCfg(sec.data)
  }, [])

  const loadLogins = useCallback(async () => {
    const [l, d] = await Promise.all([
      api.get('/admin/logins', { params: { ...(loginUser ? { user_id: loginUser } : {}), failed_only: failedOnly, limit: 500 } }),
      loginUser ? api.get(`/admin/users/${loginUser}/devices`) : Promise.resolve({ data: [] }),
    ])
    setLogins(l.data)
    setDevices(d.data)
  }, [loginUser, failedOnly])
  useEffect(() => {
    if (tab === 'logins') loadLogins().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [tab, loadLogins])

  const showLogins = (u) => {
    setLoginUser(u.id)
    setTab('logins')
  }

  const saveSecurity = (next) => {
    const on = (k) => next[k] && !secCfg[k]
    if (on('login_email_otp') && !window.confirm('Ask everyone for a code from their email each time they sign in?\n\nMake sure email is working first: without the code nobody can sign in (you included).')) return
    if (on('passcode_required') && !window.confirm('Ask everyone for a passcode each time they open the app or site? Each user chooses theirs the next time they open it.')) return
    act(async () => setSecCfg((await api.put('/admin/settings/security', next)).data), 'Sign-in security saved')
  }
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

  const resetMfa = (u) => {
    const reason = window.prompt(
      `Turn off two-step sign-in for @${u.username}? Use this when they've lost their phone and their recovery codes.\n\nThey are signed out, can sign in with their password, and get an email.\n\nReason (optional):`,
      '',
    )
    if (reason === null) return
    act(() => api.post(`/admin/users/${u.id}/mfa/reset`, { reason: reason || null }), `Two-step sign-in turned off for @${u.username}`)
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
      `Permanently delete @${u.username} and ALL their data?\n\nIn split bill groups they stay as a guest under their name, so nobody’s balance changes; groups they own pass to another member.\n\nA full backup is saved first, and you can restore it from the Backups tab.\n\nReason (optional):`,
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

  const decideDeletion = (r, approve) => {
    const comment = window.prompt(
      approve
        ? `Delete @${r.username} (${r.full_name}) and ALL their data, as they asked?\n\nIt is the same as deleting them from the Users tab: a full backup is saved first (restore it from the Backups tab), and in split bill groups they stay as a guest. They get an email.\n\nOptional note for the user:`
        : 'Reason for not deleting the account (shown to the user, optional):',
      '',
    )
    if (comment === null) return
    act(
      () => api.post(`/admin/deletion-requests/${r.id}/${approve ? 'approve' : 'reject'}`, { comment: comment || null }),
      approve ? `@${r.username} deleted, as they asked. Backup saved.` : 'Request rejected. The user has been told.',
    )
  }

  const saveOtp = (e) => {
    e.preventDefault()
    act(() => api.put('/admin/settings/otp', { signup_minutes: Number(otpCfg.signup_minutes), account_minutes: Number(otpCfg.account_minutes) }), 'Code validity saved')
  }

  const byId = Object.fromEntries(users.map((u) => [u.id, u]))
  const orgAdmins = users.filter((u) => u.role === 'ORG_ADMIN')
  const pendingUsers = users.filter((u) => u.status === 'PENDING').length
  const pendingReqs = requests.filter((r) => r.status === 'PENDING').length
  const pendingDeletions = deletions.filter((r) => r.status === 'PENDING').length
  const q = search.trim().toLowerCase()
  const shown = users.filter(
    (u) => (!filter || u.status === filter) && (!q || `${u.full_name} ${u.username} ${u.email}`.toLowerCase().includes(q)),
  )

  return (
    <div>
      <PageHeader title="Administration" subtitle="Approve and lock accounts, choose each user’s role and plan, set reporting lines and organisations, handle username and account deletion requests, and restore deleted data. Locking an organisation admin locks everyone in their organisation.">
        <div className="segmented">
          <button className={tab === 'users' ? 'on' : ''} onClick={() => setTab('users')}>
            <Users size={14} /> Users {pendingUsers > 0 && <span className="pill">{pendingUsers}</span>}
          </button>
          <button className={tab === 'requests' ? 'on' : ''} onClick={() => setTab('requests')}>
            <AtSign size={14} /> Username requests {pendingReqs > 0 && <span className="pill">{pendingReqs}</span>}
          </button>
          <button className={tab === 'deletions' ? 'on' : ''} onClick={() => setTab('deletions')}>
            <UserX size={14} /> Deletion requests {pendingDeletions > 0 && <span className="pill">{pendingDeletions}</span>}
          </button>
          <button className={tab === 'logins' ? 'on' : ''} onClick={() => setTab('logins')}>
            <History size={14} /> Sign-ins
          </button>
          <button className={tab === 'backups' ? 'on' : ''} onClick={() => setTab('backups')}>
            <DatabaseBackup size={14} /> Backups
          </button>
          <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>
            <Settings2 size={14} /> Settings
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
              <table className="table users-table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Role &amp; reporting</th>
                    <th>Plan</th>
                    <th>Joined · last sign-in</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <div className="cell-user">
                          <Avatar user={u} size={34} />
                          <div style={{ minWidth: 0 }}>
                            <b>{u.full_name}</b>
                            <small>@{u.username}</small>
                            <small title={u.email}>{u.email}</small>
                          </div>
                        </div>
                        <div className="user-badges">
                        <span className={`badge ${STATUS_CLS[u.status]}`}>{u.status.toLowerCase()}</span>{' '}
                        {!u.email_verified && (
                          <span className="badge warn" title="They haven’t entered the code we emailed them, so they can’t be approved yet">
                            <MailWarning size={11} /> email not confirmed
                          </span>
                        )}{' '}
                        {u.is_locked && (
                          <span className="badge bad" title={`Locked by ${u.locked_by} on ${fmt(u.locked_at)}${u.lock_reason ? ` · ${u.lock_reason}` : ''}`}>
                            <Lock size={11} /> locked
                          </span>
                        )}
                        {u.mfa_enabled && (
                          <span className="badge info" title="Two-step sign-in (authenticator app) is on">
                            <ShieldCheck size={11} /> 2-step
                          </span>
                        )}{' '}
                        {u.org_locked && !u.is_locked && (
                          <span className="badge bad" title={`Their organisation admin @${byId[u.org_admin_id]?.username} is locked`}>
                            <Lock size={11} /> org locked
                          </span>
                        )}
                        </div>
                      </td>
                      <td>
                        <div className="role-stack">
                        <label className="mini-label">Role</label>
                        <select
                          value={u.role}
                          disabled={u.id === me.id}
                          onChange={(e) => act(() => api.patch(`/admin/users/${u.id}`, { role: e.target.value }), 'Role updated')}
                        >
                          <option value="USER">User</option>
                          <option value="ORG_ADMIN">Org admin</option>
                          <option value="ADMIN">Admin</option>
                        </select>
                        <label className="mini-label">Reports to</label>
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
                        <label className="mini-label">Organisation</label>
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
                        </div>
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
                              ['feature_upi', 'UPI tracker'],
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
                                {(key === 'feature_accounting' || key === 'feature_upi') && u[key] && !u.feature_expense && (
                                  <span className="muted small">(needs Expenses)</span>
                                )}
                              </label>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="small">
                        <div className="muted" style={{ whiteSpace: 'nowrap' }}>Joined {u.created_at.slice(0, 10)}</div>
                        {u.last_login_at ? (
                          <button type="button" className="link-button" title="All their sign-ins and devices" onClick={() => showLogins(u)} style={{ textAlign: 'left' }}>
                            <div style={{ whiteSpace: 'nowrap' }}>{fmt(u.last_login_at)}</div>
                            <div className="muted">{[u.last_login_ip, u.last_login_device].filter(Boolean).join(' · ')}</div>
                          </button>
                        ) : (
                          <div className="muted">Never signed in</div>
                        )}
                      </td>
                      <td>
                        <div className="actions">
                          {u.status !== 'APPROVED' && (
                            <button className="btn small" disabled={!u.email_verified} title={u.email_verified ? undefined : 'Waiting for them to confirm their email'}
                                    onClick={() => act(() => api.post(`/admin/users/${u.id}/approve`), `@${u.username} approved`)}>
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
                          <button className="btn ghost small" title="Sign-ins and devices" onClick={() => showLogins(u)}>
                            <MonitorSmartphone size={14} />
                          </button>
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
                          {u.id !== me.id && u.mfa_enabled && (
                            <button className="btn ghost small" title="Turn off two-step sign-in (lost phone)" onClick={() => resetMfa(u)}>
                              <ShieldOff size={14} />
                            </button>
                          )}
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
                  {!shown.length && <EmptyRow cols={9}>No users match.</EmptyRow>}
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

      {tab === 'settings' && otpCfg && (
        <form className="card" onSubmit={saveOtp} style={{ maxWidth: 640 }}>
          <div className="card-head">
            <div>
              <h3>Email codes</h3>
              <p>How long the one-time codes we email stay valid (1 to 60 minutes). Codes already sent keep their old expiry.</p>
            </div>
          </div>
          <div className="form-grid">
            <div>
              <label>Sign-up email confirmation (minutes)</label>
              <input type="number" min={1} max={60} required value={otpCfg.signup_minutes} onChange={(e) => setOtpCfg({ ...otpCfg, signup_minutes: e.target.value })} />
              <span className="hint">New users must enter this code before you can approve them.</span>
            </div>
            <div>
              <label>Password and username changes (minutes)</label>
              <input type="number" min={1} max={60} required value={otpCfg.account_minutes} onChange={(e) => setOtpCfg({ ...otpCfg, account_minutes: e.target.value })} />
              <span className="hint">Also used for username requests sent to you.</span>
            </div>
          </div>
          <div className="form-actions">
            <button className="btn">Save</button>
          </div>
        </form>
      )}

      {tab === 'settings' && secCfg && (
        <div className="card" style={{ maxWidth: 640 }}>
          <div className="card-head">
            <div>
              <h3>Sign-in security</h3>
              <p>Rules for everyone. Each one can be turned off again at any time.</p>
            </div>
          </div>
          {[
            ['login_email_otp', 'Email code at sign-in', 'After the password (and the authenticator code, for users who use one), a 6-digit code is emailed and must be entered. It is valid as long as the password-change codes above.'],
            ['passcode_required', 'Passcode to open the app', 'Each user chooses a 4–6 digit passcode, asked whenever the app or site is opened where they are signed in, and after 5 minutes away. Five wrong passcodes sign them out everywhere.'],
          ].map(([key, title, text]) => (
            <div className="setting-row" key={key}>
              <div>
                <div className="strong">{title}</div>
                <div className="hint">{text}</div>
              </div>
              <label className="switch">
                <input type="checkbox" checked={secCfg[key]} onChange={(e) => saveSecurity({ ...secCfg, [key]: e.target.checked })} aria-label={title} />
                <span />
              </label>
            </div>
          ))}
        </div>
      )}

      {tab === 'logins' && (
        <>
          <div className="card flush">
            <div className="toolbar">
              <select value={loginUser || ''} onChange={(e) => setLoginUser(e.target.value ? Number(e.target.value) : null)} aria-label="Whose sign-ins">
                <option value="">Everyone</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name} (@{u.username})
                  </option>
                ))}
              </select>
              <label className="check">
                <input type="checkbox" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} /> Failed only
              </label>
            </div>
            {loginUser && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Device</th>
                      <th>Using</th>
                      <th>IP addresses</th>
                      <th className="num">Sign-ins</th>
                      <th>First seen</th>
                      <th>Last seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((d, i) => (
                      <tr key={i}>
                        <td>
                          <b>{d.device || 'Unknown device'}</b>
                          <div className="small muted">{d.os}</div>
                        </td>
                        <td>{d.platform === 'APP' ? <span className="badge info">App {d.app_version}</span> : d.browser || 'Browser'}</td>
                        <td className="small">{d.ips.join(', ')}</td>
                        <td className="num">{d.sign_ins}</td>
                        <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{fmt(d.first_seen)}</td>
                        <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{fmt(d.last_seen)}</td>
                      </tr>
                    ))}
                    {!devices.length && <EmptyRow cols={6}>No sign-ins recorded yet.</EmptyRow>}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div className="card flush">
            <div className="card-head">
              <div>
                <h3>Sign-in log</h3>
                <p>Every sign-in and failed try, newest first, kept for 180 days. Only admins see this.</p>
              </div>
            </div>
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>When</th>
                    {!loginUser && <th>User</th>}
                    <th>Result</th>
                    <th>IP address</th>
                    <th>Device</th>
                  </tr>
                </thead>
                <tbody>
                  {logins.map((e) => (
                    <tr key={e.id}>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(e.created_at)}</td>
                      {!loginUser && (
                        <td>
                          <button type="button" className="link-button" onClick={() => setLoginUser(e.user_id)}>
                            @{e.username}
                          </button>
                        </td>
                      )}
                      <td>
                        <span className={`badge ${e.result === 'SUCCESS' ? 'ok' : 'bad'}`}>{e.result === 'SUCCESS' ? 'signed in' : 'failed'}</span>
                        {e.detail && <div className="small muted">{e.detail}</div>}
                      </td>
                      <td>{e.ip || '—'}</td>
                      <td title={e.user_agent || ''}>
                        {e.device || 'Unknown device'}
                        <div className="small muted">
                          {[e.os, e.platform === 'APP' ? `app ${e.app_version || ''}` : e.browser].filter(Boolean).join(' · ')}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!logins.length && <EmptyRow cols={loginUser ? 4 : 5}>No sign-ins recorded yet.</EmptyRow>}
                </tbody>
              </table>
            </div>
          </div>
        </>
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

      {tab === 'deletions' && (
        <div className="card flush">
          <div className="card-head">
            <div>
              <h3>Account deletion requests</h3>
              <p>
                Users ask for their account to be deleted from their Profile page. Approving deletes it like the Users tab does: a full backup is
                saved first, and they stay as a guest in split bill groups. You can’t approve your own request; another admin has to.
              </p>
            </div>
          </div>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Reason</th>
                  <th>Status</th>
                  <th>Requested</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {deletions.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <div className="strong">{r.full_name}</div>
                      <div className="small muted">@{r.username}</div>
                    </td>
                    <td className="muted">{r.reason}</td>
                    <td>
                      <span className={`badge ${STATUS_CLS[r.status]}`}>{r.status === 'APPROVED' ? 'deleted' : r.status.toLowerCase()}</span>
                      {r.decided_by_username && (
                        <div className="small muted">
                          by @{r.decided_by_username}, {fmt(r.decided_at)}
                          {r.backup_id ? ` · backup #${r.backup_id}` : ''}
                        </div>
                      )}
                      {r.admin_comment && <div className="small muted">{r.admin_comment}</div>}
                    </td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(r.created_at)}</td>
                    <td>
                      {r.status === 'PENDING' && (
                        <div className="actions">
                          <button className="btn danger small" onClick={() => decideDeletion(r, true)} disabled={r.user_id === me.id} title={r.user_id === me.id ? 'Another admin has to approve your own request' : undefined}>
                            <Trash2 size={14} /> Approve &amp; delete
                          </button>
                          <button className="btn secondary small" onClick={() => decideDeletion(r, false)}>
                            <X size={14} /> Reject
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {!deletions.length && <EmptyRow cols={5}>No account deletion requests.</EmptyRow>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
