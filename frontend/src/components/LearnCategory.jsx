import { useState } from 'react'
import api, { errorMessage } from '../api'
import { Alert } from './ui'

/**
 * After a record's category was changed by hand: offer to use it for that payee from now on (an alias with that
 * category, so new records from SMS / UPI alerts and imports get it), and optionally for its past records too.
 *
 * `offer`: { kind: 'sent' | 'received', name, payee_key, alias_id, category }
 */
export function learnable(rec, oldCategory, newCategory) {
  return newCategory && newCategory !== oldCategory && (rec.alias_id || (rec.payee_key && rec.payee_key.length >= 2 && rec.payee_key !== '-'))
}

export default function LearnCategory({ offer, onDone, onClose }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  if (!offer) return null
  const field = offer.kind === 'received' ? 'income_category' : 'category'

  const save = async (past) => {
    setBusy(true)
    setError(null)
    try {
      if (offer.alias_id) {
        await api.put(`/aliases/${offer.alias_id}`, { [field]: offer.category })
        onDone(`From now on “${offer.name}” goes in ${offer.category}, and so do its records that had the old category.`)
      } else {
        const r = await api.post('/aliases', {
          match_key: offer.payee_key,
          alias: offer.name,
          [field]: offer.category,
          apply_all_existing: past,
          set_category_existing: past,
        })
        const n = r.data.renamed > 1 ? ` ${r.data.renamed - 1} past record${r.data.renamed === 2 ? '' : 's'} updated too.` : ''
        const locked = r.data.skipped_locked ? ` ${r.data.skipped_locked} in a locked accounting period were left as they are.` : ''
        onDone(`From now on “${offer.name}” goes in ${offer.category}.${n}${locked} Change it any time in Settings → Transaction aliases.`)
      }
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  return (
    <Alert type="info">
      <div>
        <b>Always put “{offer.name}” in {offer.category}?</b>{' '}
        <span className="muted">
          New {offer.kind === 'received' ? 'money received from' : 'payments to'} this payee, from bank alerts or imports, will get this category.
        </span>
      </div>
      {error && <div style={{ color: 'var(--danger)', marginTop: 6 }}>{error}</div>}
      <div className="actions" style={{ marginTop: 8, flexWrap: 'wrap' }}>
        <button className="btn small" disabled={busy} onClick={() => save(false)}>
          Yes, from now on
        </button>
        {!offer.alias_id && (
          <button className="btn secondary small" disabled={busy} onClick={() => save(true)} title={`Past records from this payee are also named “${offer.name}”`}>
            Yes, and past ones too
          </button>
        )}
        <button className="btn ghost small" disabled={busy} onClick={onClose}>
          No, just this one
        </button>
      </div>
    </Alert>
  )
}
