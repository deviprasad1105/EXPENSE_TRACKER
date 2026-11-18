import { useCallback, useEffect, useMemo, useState } from 'react'
import api, { errorMessage, money, niceDate } from '../api'
import { useAuth } from '../auth'
import { KINDS, ONE } from '../investmentKinds'
import Modal from './Modal'
import { announceChange } from './QuickAdd'

/**
 * After saving an expense or a transfer, ask whether it was buying an investment (a SIP, shares, money sent to a
 * broker...). The server decides which ones look like it; nothing is asked for the rest.
 */
export const checkInvestments = ({ expenseIds = null, transferIds = null } = {}) =>
  window.dispatchEvent(new CustomEvent('investment-check', { detail: { expenseIds, transferIds } }))

/** Mounted once (Layout): shows the dialog for what was just saved, one payment at a time. */
export default function InvestPromptHost() {
  const { params } = useAuth()
  const [queue, setQueue] = useState([])

  useEffect(() => {
    const onCheck = async (e) => {
      const { expenseIds, transferIds } = e.detail || {}
      const q = {}
      if (expenseIds) q.expense_ids = expenseIds.join(',')
      if (transferIds) q.transfer_ids = transferIds.join(',')
      if (!q.expense_ids && !q.transfer_ids) return
      try {
        const r = await api.get('/investments/pending', { params: { ...params, ...q } })
        if (r.data.items.length) setQueue((old) => [...old, ...r.data.items.filter((i) => !old.some((o) => o.source === i.source && o.id === i.id))])
      } catch {
        /* the user can still add it from the Investments page */
      }
    }
    window.addEventListener('investment-check', onCheck)
    return () => window.removeEventListener('investment-check', onCheck)
  }, [params])

  if (!queue.length) return null
  const next = () => setQueue((q) => q.slice(1))
  return <InvestDialog key={`${queue[0].source}-${queue[0].id}`} item={queue[0]} left={queue.length - 1} onDone={next} onClose={next} />
}

/** "₹5,000 · SIP HSBC (Mutual funds) · 18 Nov · from Axis": add it to a holding, as a new one, or skip it. */
export function InvestDialog({ item, left = 0, onDone, onClose }) {
  const { params } = useAuth()
  const [holdings, setHoldings] = useState(null)
  const [mode, setMode] = useState(null) // ADD or NEW
  const [holdingId, setHoldingId] = useState('')
  const [units, setUnits] = useState('')
  const [f, setF] = useState({ kind: 'MUTUAL_FUND', name: '', platform: '', parent_id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .get('/investments', { params })
      .then((r) => {
        const all = r.data.holdings.filter((h) => h.is_active)
        setHoldings(all)
        // A holding (or broker) named in the payment is the likely one
        const text = item.label.toLowerCase()
        const guess = all.find((h) => h.name.split(/[\s:(]+/).some((w) => w.length >= 4 && text.includes(w.toLowerCase()))) ||
          all.find((h) => h.platform && text.includes(h.platform.toLowerCase()))
        setHoldingId(guess ? String(guess.id) : '')
        setMode(all.length ? 'ADD' : 'NEW')
        if (/mutual|sip|\bmf\b/i.test(item.label)) setF((x) => ({ ...x, kind: 'MUTUAL_FUND' }))
        else if (/crypto|coin|bitcoin|wazirx/i.test(item.label)) setF((x) => ({ ...x, kind: 'CRYPTO' }))
        else if (/gold/i.test(item.label)) setF((x) => ({ ...x, kind: 'GOLD' }))
        else if (/share|stock|groww|zerodha|nuvama|upstox|broker|demat/i.test(item.label)) setF((x) => ({ ...x, kind: 'STOCK' }))
      })
      .catch((e) => setError(errorMessage(e)))
  }, [params, item])

  // Top-level lines, each followed by the lines under it, grouped by type
  const options = useMemo(() => {
    if (!holdings) return []
    const kids = {}
    for (const h of holdings) if (h.parent_id != null) (kids[h.parent_id] ||= []).push(h)
    return Object.keys(KINDS)
      .map((k) => [k, holdings.filter((h) => h.parent_id == null && h.kind === k).flatMap((h) => [h, ...(kids[h.id] || [])])])
      .filter(([, list]) => list.length)
  }, [holdings])
  const parents = (holdings || []).filter((h) => h.parent_id == null)

  const settle = useCallback(
    async (body) => {
      setBusy(true)
      setError('')
      try {
        await api.post(`/investments/pending/${item.source.toLowerCase()}/${item.id}`, body)
        announceChange()
        onDone(body.action)
      } catch (e) {
        setError(errorMessage(e))
      } finally {
        setBusy(false)
      }
    },
    [item, onDone],
  )

  const submit = (e) => {
    e.preventDefault()
    if (mode === 'ADD') {
      if (!holdingId) return setError('Choose the investment it goes into')
      return settle({ action: 'ADD', holding_id: Number(holdingId), units: units || null })
    }
    if (!f.name.trim()) return setError('Give the new investment a name')
    return settle({
      action: 'NEW',
      holding: { kind: f.kind, name: f.name.trim(), platform: f.platform.trim() || null, units: units || null, parent_id: f.parent_id ? Number(f.parent_id) : null },
    })
  }

  return (
    <Modal
      title="Add to your investments?"
      subtitle={`${money(item.amount)} · ${item.label} · ${niceDate(item.date)}${item.account ? ` · from ${item.account}` : ''}${left ? ` · ${left} more after this` : ''}`}
      onClose={onClose}
    >
      {!holdings && !error && <p className="muted">Loading…</p>}
      {holdings && (
        <form className="invest-prompt" onSubmit={submit}>
          <div className="segmented" role="tablist" aria-label="Existing or new">
            <button type="button" role="tab" aria-selected={mode === 'ADD'} className={mode === 'ADD' ? 'on' : ''} disabled={!holdings.length} onClick={() => setMode('ADD')}>
              Add to one I have
            </button>
            <button type="button" role="tab" aria-selected={mode === 'NEW'} className={mode === 'NEW' ? 'on' : ''} onClick={() => setMode('NEW')}>
              New investment
            </button>
          </div>

          {mode === 'ADD' ? (
            <div className="form-grid">
              <div style={{ gridColumn: '1 / -1' }}>
                <label>Goes into</label>
                <select value={holdingId} onChange={(e) => setHoldingId(e.target.value)} required>
                  <option value="">Choose…</option>
                  {options.map(([k, list]) => (
                    <optgroup key={k} label={KINDS[k]}>
                      {list.map((h) => (
                        <option key={h.id} value={h.id}>
                          {h.parent_id != null ? `   └ ${h.name}` : h.name}
                          {h.platform && h.parent_id == null ? ` · ${h.platform}` : ''}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <span className="hint">Its amount invested goes up by {money(item.amount)}, and the line it’s under too.</span>
              </div>
              <div>
                <label>Units bought</label>
                <input type="number" min="0" step="any" value={units} onChange={(e) => setUnits(e.target.value)} placeholder="If you know" />
              </div>
            </div>
          ) : (
            <div className="form-grid">
              <div>
                <label>Type</label>
                <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
                  {Object.entries(ONE).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>Name</label>
                <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} maxLength={120} required placeholder="e.g. Parag Parikh Flexi Cap" />
              </div>
              <div>
                <label>Held at</label>
                <input value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value })} maxLength={60} placeholder="Groww, Zerodha, CoinDCX…" />
              </div>
              <div>
                <label>{f.kind === 'GOLD' ? 'Grams' : 'Units'}</label>
                <input type="number" min="0" step="any" value={units} onChange={(e) => setUnits(e.target.value)} placeholder="If you know" />
              </div>
              {parents.length > 0 && (
                <div style={{ gridColumn: '1 / -1' }}>
                  <label>Part of</label>
                  <select value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
                    <option value="">Nothing (counts on its own)</option>
                    {parents.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <span className="hint">E.g. a new stock under your broker’s total.</span>
                </div>
              )}
            </div>
          )}

          {error && <p className="error-text">{error}</p>}
          <div className="form-actions invest-prompt-actions">
            <button type="button" className="btn ghost" disabled={busy} onClick={() => settle({ action: 'SKIP' })}>
              Not an investment
            </button>
            <span className="spacer" />
            <button type="button" className="btn secondary" disabled={busy} onClick={onClose} title="It stays on the Investments page to add later">
              Later
            </button>
            <button className="btn" disabled={busy}>
              {mode === 'ADD' ? 'Add' : 'Add investment'}
            </button>
          </div>
        </form>
      )}
      {!holdings && error && <p className="error-text">{error}</p>}
    </Modal>
  )
}
