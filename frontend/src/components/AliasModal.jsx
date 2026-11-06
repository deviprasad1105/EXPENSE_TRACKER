import { useEffect, useMemo, useState } from 'react'
import api, { errorMessage, money } from '../api'
import { Msg } from './ui'
import Modal from './Modal'

/**
 * Name a payee (new alias), or rename the old records an existing alias matches but hasn't renamed yet.
 *
 * New alias: `initial` = { match_key, alias, category }. The records it would rename are listed, all ticked, so the
 * user chooses whether (and which) old records take the alias. Existing alias: `alias` = the saved alias; only the
 * old-records list is shown.
 */
export default function AliasModal({ initial, alias, categories: given, onClose, onSaved }) {
  const [form, setForm] = useState({ match_key: '', alias: '', category: '', ...initial })
  const [records, setRecords] = useState(null)
  const [picked, setPicked] = useState(new Set())
  const [renameOld, setRenameOld] = useState(true)
  const [categoryOld, setCategoryOld] = useState(false)
  const [kind, setKind] = useState('all') // all, EXPENSE (sent) or FUND (received)
  const [categories, setCategories] = useState(given || [])

  useEffect(() => {
    if (!given) api.get('/expenses/categories').then((r) => setCategories(r.data)).catch(() => {})
  }, [given])
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const existing = !!alias
  const key = existing ? alias.match_key : form.match_key.trim()

  // The records the alias would rename: refreshed as the user types the name / UPI ID to look for
  useEffect(() => {
    if (key.length < 2) return setRecords([])
    let cancelled = false
    const t = setTimeout(async () => {
      try {
        const r = existing
          ? await api.get(`/aliases/${alias.id}/records`, { params: { unnamed: true } })
          : await api.get('/aliases/matches', { params: { match_key: key } })
        if (cancelled) return
        setRecords(r.data)
        setPicked(new Set(r.data.map((x) => `${x.kind}:${x.id}`)))
      } catch (err) {
        if (!cancelled) setMsg({ type: 'error', text: errorMessage(err) })
      }
    }, existing ? 0 : 350)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [key, existing, alias?.id])

  const ids = useMemo(() => {
    const out = { expense_ids: [], fund_ids: [] }
    if (!renameOld && !existing) return out
    for (const p of picked) {
      const [kind, id] = p.split(':')
      ;(kind === 'EXPENSE' ? out.expense_ids : out.fund_ids).push(Number(id))
    }
    return out
  }, [picked, renameOld, existing])

  const toggle = (k) =>
    setPicked((cur) => {
      const next = new Set(cur)
      next.has(k) ? next.delete(k) : next.add(k)
      return next
    })
  const allOn = records?.length > 0 && picked.size === records.length

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const r = existing
        ? await api.post(`/aliases/${alias.id}/apply`, { ...ids, set_category_existing: categoryOld })
        : await api.post('/aliases', { match_key: form.match_key, alias: form.alias, category: form.category || null, set_category_existing: categoryOld, ...ids })
      const skipped = r.data.skipped_locked ? ` ${r.data.skipped_locked} in a locked accounting period were left as they are.` : ''
      onSaved(`${existing ? alias.alias : form.alias}: ${r.data.renamed} old record${r.data.renamed === 1 ? '' : 's'} renamed.${skipped}`)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      setBusy(false)
    }
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const count = ids.expense_ids.length + ids.fund_ids.length

  return (
    <Modal
      wide
      title={existing ? `Rename old records to “${alias.alias}”` : 'Set an alias'}
      subtitle={
        existing
          ? 'These records match the alias but still show their original description. Tick the ones to rename.'
          : 'Every transaction to or from this payee will show the alias, including future ones.'
      }
      onClose={onClose}
    >
      <Msg msg={msg} />
      <form onSubmit={save}>
        {!existing && (
          <div className="inline-form" style={{ marginTop: 0 }}>
            <div>
              <label>When a transaction has</label>
              <input value={form.match_key} onChange={set('match_key')} placeholder="UPI ID or name, e.g. ramesh@ybl" required minLength={2} maxLength={120} />
            </div>
            <div>
              <label>Show it as</label>
              <input value={form.alias} onChange={set('alias')} placeholder="e.g. Tea" required maxLength={100} autoFocus />
            </div>
            <div>
              <label>Category (optional)</label>
              <input value={form.category} onChange={set('category')} list="alias-categories" placeholder="Pick or type a new one" maxLength={50} />
              <datalist id="alias-categories">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
          </div>
        )}

        {records === null ? (
          <p className="muted">Looking for matching records…</p>
        ) : records.length === 0 ? (
          <p className="muted">{key.length < 2 ? 'Type a name or UPI ID to see the records it matches.' : 'No existing records without an alias match this. New ones will get the alias.'}</p>
        ) : (
          <>
            {!existing && (
              <label className="check" style={{ margin: '12px 0 8px' }}>
                <input type="checkbox" checked={renameOld} onChange={(e) => setRenameOld(e.target.checked)} />
                <b>Also rename {records.length} existing record{records.length === 1 ? '' : 's'}</b>
                <span className="muted">(untick any you want to keep as they are)</span>
              </label>
            )}
            {(renameOld || existing) && (existing ? alias.category : form.category) && (
              <label className="check" style={{ margin: '0 0 8px' }}>
                <input type="checkbox" checked={categoryOld} onChange={(e) => setCategoryOld(e.target.checked)} />
                Also put the ticked old expenses in <b>{existing ? alias.category : form.category}</b>
                <span className="muted">(new ones always get it)</span>
              </label>
            )}
            {(renameOld || existing) && records.some((x) => x.kind === 'FUND') && records.some((x) => x.kind === 'EXPENSE') && (
              <div className="segmented" style={{ marginBottom: 8 }} role="tablist" aria-label="Sent or received">
                {[
                  ['all', `All (${records.length})`],
                  ['EXPENSE', `Sent (${records.filter((x) => x.kind === 'EXPENSE').length})`],
                  ['FUND', `Received (${records.filter((x) => x.kind === 'FUND').length})`],
                ].map(([k, l]) => (
                  <button type="button" key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
                    {l}
                  </button>
                ))}
              </div>
            )}
            {(renameOld || existing) && (
              <div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}>
                <table className="table mini-table">
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          aria-label="Select all"
                          checked={allOn}
                          onChange={() => setPicked(allOn ? new Set() : new Set(records.map((x) => `${x.kind}:${x.id}`)))}
                        />
                      </th>
                      <th>Date</th>
                      <th>Description now</th>
                      <th>Type · category</th>
                      <th className="num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.filter((x) => kind === 'all' || x.kind === kind).map((x) => {
                      const k = `${x.kind}:${x.id}`
                      return (
                        <tr key={k} className={picked.has(k) ? '' : 'dim'} onClick={() => toggle(k)} style={{ cursor: 'pointer' }}>
                          <td>
                            <input type="checkbox" checked={picked.has(k)} onChange={() => toggle(k)} onClick={(e) => e.stopPropagation()} />
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>{x.date}</td>
                          <td className="strong" style={{ wordBreak: 'break-word' }}>
                            {x.name} {x.origin === 'UPI' && <span className="badge info">UPI</span>}
                          </td>
                          <td>{x.kind === 'EXPENSE' ? <><span className="badge bad">Sent</span> {x.category}</> : <span className="badge ok">Received</span>}</td>
                          <td className={`num ${x.kind === 'FUND' ? 'net-pos' : ''}`}>
                            {x.kind === 'FUND' ? '+' : ''}
                            {money(x.amount)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {records.length >= 500 && <p className="hint">Showing the latest 500. Save, then open the alias again for the rest.</p>}
          </>
        )}

        <div className="form-actions">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" disabled={busy || (existing && !count)}>
            {existing ? `Rename ${count} record${count === 1 ? '' : 's'}` : count ? `Save alias & rename ${count}` : 'Save alias'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
