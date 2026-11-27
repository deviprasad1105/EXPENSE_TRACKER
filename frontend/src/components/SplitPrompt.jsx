import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Split, User, X } from 'lucide-react'
import api, { errorMessage, money, niceDate } from '../api'
import { useAuth } from '../auth'
import Modal from './Modal'
import { Alert } from './ui'

const round2 = (n) => Math.round(n * 100) / 100

/**
 * "Split this?" — shown when you paid a shop or UPI ID one of your split groups shares (Split group → Members →
 * Shared shops). Lists the group's members (ticked as the payee says), with amounts you can change, and three
 * answers: Split, Only me (added to the group as yours alone) and Cancel (not added to the group). Checked when the
 * app opens, comes back to the front, when a payment notification is tapped, and every minute.
 */
export default function SplitPrompt() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [prompts, setPrompts] = useState([])
  // Closed with × : asked again the next time the app is opened, not every minute meanwhile
  const [later, setLater] = useState(() => new Set())

  const load = useCallback(() => api.get('/splits/prompts').then((r) => setPrompts(r.data)).catch(() => {}), [])
  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    const onVisible = () => !document.hidden && load()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('autotrackRoute', load)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('autotrackRoute', load)
    }
  }, [load, user?.id])

  const waiting = prompts.filter((p) => !later.has(p.id))
  const current = waiting[0]
  if (!current) return null
  const done = (result) => {
    if (result === 'later') setLater((s) => new Set(s).add(current.id))
    else setPrompts((list) => list.filter((p) => p.id !== current.id))
    if (result?.go) navigate(`/splits/${result.go}`)
  }
  return <PromptDialog key={current.id} p={current} left={waiting.length - 1} meId={user.id} onDone={done} />
}

function PromptDialog({ p, left, meId, onDone }) {
  const total = Number(p.amount)
  const [rows, setRows] = useState(() => p.members.map((m) => ({ person: m, on: p.default_member_ids.includes(m.id), amount: '' })))
  const [custom, setCustom] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const chosen = rows.filter((r) => r.on)
  const typed = chosen.filter((r) => r.amount !== '')
  const fixed = round2(typed.reduce((t, r) => t + Number(r.amount || 0), 0))
  const blank = chosen.length - typed.length
  const each = blank ? round2((total - fixed) / blank) : 0
  const shareOf = (r) => (!r.on ? 0 : custom && r.amount !== '' ? Number(r.amount) : custom ? each : round2(total / chosen.length))
  const off = custom && (fixed > total + 0.001 || (!blank && Math.abs(fixed - total) > 0.001))

  const answer = async (action) => {
    setBusy(action)
    setError('')
    try {
      const shares = chosen.map((r) => ({ member_id: r.person.id, amount: custom && r.amount !== '' ? r.amount : null }))
      const { data } = await api.post(`/splits/prompts/${p.id}`, { action, shares, title: p.label })
      onDone(action === 'SPLIT' ? { go: data.group_id } : null)
    } catch (e) {
      setError(errorMessage(e))
      // Already answered elsewhere (on the website, another phone): move on
      if (e?.response?.status === 404) onDone(null)
    } finally {
      setBusy('')
    }
  }
  const set = (i, patch) => setRows((list) => list.map((r, j) => (i === j ? { ...r, ...patch } : r)))

  return (
    <Modal
      title={`Split ${money(total)} at ${p.label}?`}
      subtitle={`${p.group.name} · ${niceDate(p.date, { relative: true })}${p.vpa ? ` · ${p.vpa}` : ''}${left ? ` · ${left} more waiting` : ''}`}
      onClose={() => onDone('later')}
    >
      {error && <Alert type="error">{error}</Alert>}
      <div className="split-prompt-list">
        {rows.map((r, i) => (
          <div key={r.person.id} className="split-prompt-row">
            <label className="check grow">
              <input type="checkbox" checked={r.on} onChange={(e) => set(i, { on: e.target.checked })} />
              {r.person.user_id === meId ? 'You' : r.person.full_name}
              {r.person.is_guest && <span className="muted small"> (guest)</span>}
            </label>
            {custom && r.on ? (
              <input
                type="number"
                min="0"
                step="0.01"
                inputMode="decimal"
                value={r.amount}
                placeholder={String(each)}
                onChange={(e) => set(i, { amount: e.target.value })}
                aria-label={`Amount for ${r.person.full_name}`}
                style={{ width: 110 }}
              />
            ) : (
              <span className="num muted">{r.on ? money(shareOf(r)) : '—'}</span>
            )}
          </div>
        ))}
      </div>
      <button type="button" className="link-button" onClick={() => setCustom(!custom)} style={{ margin: '8px 0' }}>
        {custom ? 'Split equally instead' : 'Change the amounts'}
      </button>
      {off && <Alert type="warn">The amounts add up to {money(fixed)}, not {money(total)}.</Alert>}
      <div className="form-actions" style={{ flexWrap: 'wrap' }}>
        <button type="button" className="btn ghost" disabled={!!busy} onClick={() => answer('SKIP')} title="Don’t add it to the group">
          <X size={16} /> Cancel
        </button>
        <button type="button" className="btn secondary" disabled={!!busy} onClick={() => answer('SELF')} title="Add it to the group as yours alone">
          <User size={16} /> {busy === 'SELF' ? 'Adding…' : 'Only me'}
        </button>
        <button type="button" className="btn" disabled={!!busy || !chosen.length || off} onClick={() => answer('SPLIT')}>
          <Split size={16} /> {busy === 'SPLIT' ? 'Splitting…' : `Split with ${chosen.length}`}
        </button>
      </div>
    </Modal>
  )
}
