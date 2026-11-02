import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, Clock, KeyRound, Lock, Send, Trash2, UserRound } from 'lucide-react'
import api, { errorMessage } from '../api'
import { useAuth } from '../auth'
import Avatar from '../components/Avatar'
import { Alert, Msg, PageHeader } from '../components/ui'

const fmt = (d) => (d ? new Date(d).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '')
const STATUS_CLS = { PENDING: 'warn', APPROVED: 'ok', REJECTED: 'bad' }

/** Crop to a centred square and shrink to 256×256 JPEG before upload. */
function resizeImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const size = 256
      const side = Math.min(img.width, img.height)
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = size
      canvas.getContext('2d').drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size)
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not process image'))), 'image/jpeg', 0.9)
      URL.revokeObjectURL(img.src)
    }
    img.onerror = () => reject(new Error('That file is not a valid image'))
    img.src = URL.createObjectURL(file)
  })
}

export default function Profile() {
  const { user, updateUser } = useAuth()
  const [profile, setProfile] = useState(null)
  const [requests, setRequests] = useState([])
  const [details, setDetails] = useState({ full_name: user.full_name, email: user.email })
  const [uname, setUname] = useState({ new_username: '', password: '', reason: '' })
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' })
  const [msgs, setMsgs] = useState({})
  const fileRef = useRef(null)

  const say = (key, type, text) => setMsgs((m) => ({ ...m, [key]: { type, text } }))

  const load = useCallback(async () => {
    const [p, r] = await Promise.all([api.get('/profile'), api.get('/profile/username-requests')])
    setProfile(p.data)
    setRequests(r.data)
    updateUser(p.data.user)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const run = async (key, fn, ok) => {
    try {
      await fn()
      say(key, 'ok', ok)
      return true
    } catch (e) {
      say(key, 'error', errorMessage(e))
      return false
    }
  }

  const uploadPhoto = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    await run('photo', async () => {
      const blob = await resizeImage(file)
      const fd = new FormData()
      fd.append('file', blob, 'avatar.jpg')
      const res = await api.put('/profile/avatar', fd)
      updateUser(res.data)
    }, 'Profile photo updated')
  }

  const removePhoto = () =>
    run('photo', async () => updateUser((await api.delete('/profile/avatar')).data), 'Profile photo removed')

  const saveDetails = (e) => {
    e.preventDefault()
    run('details', async () => updateUser((await api.patch('/profile', details)).data), 'Profile saved')
  }

  const changeUsername = async (e) => {
    e.preventDefault()
    if (!window.confirm(`Change your username to "${uname.new_username}"?\n\nYou won’t be able to change it again for 15 days without asking an admin.`)) return
    const ok = await run('username', async () => {
      const res = await api.post('/profile/username', { new_username: uname.new_username, password: uname.password })
      updateUser(res.data)
    }, 'Username changed. Use the new one next time you sign in.')
    if (ok) setUname({ new_username: '', password: '', reason: '' })
    load()
  }

  const requestUsername = async (e) => {
    e.preventDefault()
    const ok = await run('username', () => api.post('/profile/username-requests', { new_username: uname.new_username, reason: uname.reason || null }), 'Request sent to the admin')
    if (ok) setUname({ new_username: '', password: '', reason: '' })
    load()
  }

  const cancelRequest = async (r) => {
    await run('username', () => api.delete(`/profile/username-requests/${r.id}`), 'Request cancelled')
    load()
  }

  const changePassword = async (e) => {
    e.preventDefault()
    if (pw.new_password !== pw.confirm) return say('password', 'error', 'New passwords do not match')
    const ok = await run('password', async () => {
      const res = await api.post('/profile/password', { current_password: pw.current_password, new_password: pw.new_password })
      // Every other device is signed out; this one keeps working with the new token
      localStorage.setItem('token', res.data.access_token)
    }, 'Password changed. Other devices have been signed out.')
    if (ok) setPw({ current_password: '', new_password: '', confirm: '' })
  }

  if (!profile) return <div className="muted">Loading…</div>
  const pending = profile.pending_username_request

  return (
    <div style={{ maxWidth: 860 }}>
      <PageHeader title="Profile" subtitle="Your photo, personal details and sign-in information." />

      <div className="card">
        <div className="card-head">
          <div>
            <h3>Profile photo</h3>
            <p>JPG, PNG, WEBP or GIF. It is cropped to a square.</p>
          </div>
        </div>
        <Msg msg={msgs.photo} />
        <div className="avatar-edit">
          <Avatar user={user} size={88} />
          <div className="actions" style={{ justifyContent: 'flex-start' }}>
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={uploadPhoto} />
            <button className="btn secondary" onClick={() => fileRef.current.click()}>
              <Camera size={16} /> {user.avatar_updated_at ? 'Change photo' : 'Upload photo'}
            </button>
            {user.avatar_updated_at && (
              <button className="btn danger" onClick={removePhoto}>
                <Trash2 size={16} /> Remove
              </button>
            )}
          </div>
        </div>
      </div>

      <form className="card" onSubmit={saveDetails}>
        <div className="card-head">
          <div>
            <h3>Personal details</h3>
            <p>
              {profile.manager ? (
                <>
                  You report to <b>{profile.manager.full_name}</b> (@{profile.manager.username}).
                </>
              ) : (
                'You do not report to anyone.'
              )}
            </p>
          </div>
          <UserRound size={20} className="muted" />
        </div>
        <Msg msg={msgs.details} />
        <div className="form-grid">
          <div>
            <label>Full name</label>
            <input value={details.full_name} onChange={(e) => setDetails({ ...details, full_name: e.target.value })} required />
          </div>
          <div>
            <label>Email</label>
            <input type="email" value={details.email} onChange={(e) => setDetails({ ...details, email: e.target.value })} required />
          </div>
        </div>
        <div className="form-actions">
          <button className="btn">Save changes</button>
        </div>
      </form>

      <div className="card">
        <div className="card-head">
          <div>
            <h3>Username</h3>
            <p>
              Current username: <b>@{user.username}</b>. After a change you have to wait 15 days to change it again, or ask an admin.
            </p>
          </div>
          {profile.can_change_username ? <KeyRound size={20} className="muted" /> : <Lock size={20} className="muted" />}
        </div>
        <Msg msg={msgs.username} />

        {profile.can_change_username ? (
          <form className="form-grid" onSubmit={changeUsername}>
            <div>
              <label>New username</label>
              <input value={uname.new_username} onChange={(e) => setUname({ ...uname, new_username: e.target.value })} pattern="[A-Za-z0-9_.\-]+" minLength={3} required />
            </div>
            <div>
              <label>Confirm with your password</label>
              <input type="password" value={uname.password} onChange={(e) => setUname({ ...uname, password: e.target.value })} required autoComplete="current-password" />
            </div>
            <div className="span-all form-actions">
              <button className="btn">Change username</button>
            </div>
          </form>
        ) : (
          <>
            <Alert type="warn">
              You changed your username on {fmt(user.username_changed_at)}. You can change it yourself again on{' '}
              <b>{fmt(profile.username_locked_until)}</b>. Until then, you can ask an admin to change it.
            </Alert>
            {pending ? (
              <div className="row-between">
                <span>
                  <Clock size={16} className="muted" style={{ verticalAlign: -3 }} /> Your request to change to <b>@{pending.requested_username}</b> is waiting for an admin.
                </span>
                <button className="btn secondary small" onClick={() => cancelRequest(pending)}>
                  Cancel request
                </button>
              </div>
            ) : (
              <form className="form-grid" onSubmit={requestUsername}>
                <div>
                  <label>Requested username</label>
                  <input value={uname.new_username} onChange={(e) => setUname({ ...uname, new_username: e.target.value })} pattern="[A-Za-z0-9_.\-]+" minLength={3} required />
                </div>
                <div>
                  <label>Reason (optional)</label>
                  <input value={uname.reason} onChange={(e) => setUname({ ...uname, reason: e.target.value })} />
                </div>
                <div className="span-all form-actions">
                  <button className="btn">
                    <Send size={16} /> Send request to admin
                  </button>
                </div>
              </form>
            )}
          </>
        )}

        {requests.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 20 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Requested</th>
                  <th>From</th>
                  <th>Status</th>
                  <th>Admin note</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id}>
                    <td className="strong">@{r.requested_username}</td>
                    <td>@{r.current_username}</td>
                    <td>
                      <span className={`badge ${STATUS_CLS[r.status]}`}>{r.status.toLowerCase()}</span>
                    </td>
                    <td className="muted">{r.admin_comment}</td>
                    <td className="muted">{fmt(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <form className="card" onSubmit={changePassword}>
        <div className="card-head">
          <div>
            <h3>Password</h3>
            <p>Use at least 8 characters. Changing it signs you out on every other device.</p>
          </div>
          <Lock size={20} className="muted" />
        </div>
        <Msg msg={msgs.password} />
        <div className="form-grid">
          <div>
            <label>Current password</label>
            <input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} required autoComplete="current-password" />
          </div>
          <div>
            <label>New password</label>
            <input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} minLength={8} required autoComplete="new-password" />
          </div>
          <div>
            <label>Confirm new password</label>
            <input type="password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} minLength={8} required autoComplete="new-password" />
          </div>
        </div>
        <div className="form-actions">
          <button className="btn">Update password</button>
        </div>
      </form>
    </div>
  )
}
