import { useEffect, useState } from 'react'
import { Eye, Undo2 } from 'lucide-react'
import api, { errorMessage, money } from '../api'
import Modal from '../components/Modal'
import { EmptyRow, Msg, PageHeader } from '../components/ui'
import { ImportTabs } from './Import'

const BATCH_STATUS = {
  IN_PROGRESS: ['info', 'Interrupted'],
  COMPLETED: ['ok', 'Imported'],
  COMPLETED_WITH_ERRORS: ['warn', 'Imported with errors'],
  FAILED: ['bad', 'Nothing imported'],
  UNDONE: ['info', 'Undone'],
  PARTIALLY_UNDONE: ['warn', 'Partly undone'],
}
const ROW_STATUS = {
  IMPORTED: ['ok', 'Imported'],
  SKIPPED: ['info', 'Left out'],
  FAILED: ['bad', 'Failed'],
  UNDONE: ['info', 'Undone'],
  KEPT: ['warn', 'Kept by Undo'],
}
const KIND = { EXPENSE: 'Expense', INCOME: 'Income', SPLIT: 'Split bill', TRANSFER: 'Transfer', OPENING: 'Opening balance' }
const when = (v) => (v ? new Date(v).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '')
const day = (v) => (v ? new Date(v).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—')

export default function ImportHistory() {
  const [batches, setBatches] = useState(null)
  const [msg, setMsg] = useState(null)
  const [open, setOpen] = useState(null)

  const load = async () => {
    try {
      setBatches((await api.get('/imports')).data)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }
  useEffect(() => {
    load()
  }, [])

  const undoable = (b) => ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'PARTIALLY_UNDONE', 'IN_PROGRESS'].includes(b.status) && b.rows_imported > b.rows_undone

  const undo = async (b) => {
    let detail
    try {
      detail = (await api.get(`/imports/${b.id}`)).data
    } catch (err) {
      return setMsg({ type: 'error', text: errorMessage(err) })
    }
    const keeps = detail.undo_keeps.map((k) => `• ${k.rows} kept: ${k.reason}`).join('\n')
    if (!detail.undo_removes) {
      return setMsg({ type: 'warn', text: `Nothing can be undone in ${b.filename}. ${detail.undo_keeps.map((k) => `${k.rows}: ${k.reason}`).join('; ')}` })
    }
    const books = detail.books
      ? '\n\nYour accounting books keep a record of this import and its reversal (as in Tally), so the earlier years it added stay, and accounts it created are deactivated rather than deleted.'
      : ''
    if (!window.confirm(`Undo ${b.filename}?\n\n${detail.undo_removes} record(s) it created will be removed.${keeps ? `\n${keeps}` : ''}\n\nOther records in the app are not touched.${books}`)) return
    // A big import is undone a few hundred records per request
    let removed = 0
    let kept = 0
    let gone = 0
    let last = null
    try {
      for (;;) {
        const res = (await api.post(`/imports/${b.id}/undo`)).data
        last = res
        gone += res.already_deleted || 0
        removed += res.removed
        kept += res.kept
        if (!res.remaining) break
        setMsg({ type: 'info', text: `Undoing ${b.filename}… ${removed} removed, ${res.remaining} to go` })
      }
      const dropped = last?.accounts_removed || []
      const off = last?.accounts_deactivated || []
      const accounts = [
        dropped.length ? ` Account(s) it created removed: ${dropped.join(', ')}.` : '',
        off.length ? ` Deactivated (they have entries in your books): ${off.join(', ')}.` : '',
      ].join('')
      const parts = [
        removed || !gone ? `${removed} record(s) removed` : '',
        gone ? `${gone} already deleted before the Undo` : '',
        kept ? `${kept} kept (see the details)` : '',
      ].filter(Boolean)
      setMsg({ type: 'ok', text: `${parts.join(', ')}.${accounts}` })
    } catch (err) {
      setMsg({ type: 'error', text: `${errorMessage(err)}${removed ? ` (${removed} record(s) were removed before this; Undo again to continue)` : ''}` })
    }
    load()
  }

  return (
    <div>
      <PageHeader title="Import history" subtitle="Every file you imported, what came in, what was left out and why. Undo removes only the records an import created.">
        <ImportTabs active="history" />
      </PageHeader>
      <Msg msg={msg} />
      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Uploaded</th>
                <th>Status</th>
                <th className="num">Imported</th>
                <th className="num">Left out</th>
                <th className="num">Failed</th>
                <th className="num">Spending</th>
                <th className="num">Received</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(batches || []).map((b) => {
                const [cls, label] = BATCH_STATUS[b.status] || ['info', b.status]
                return (
                  <tr key={b.id}>
                    <td>
                      <span className="strong">{b.filename}</span>
                      <div className="muted small">{b.file_type.toUpperCase()} · {b.rows_total} row(s)</div>
                    </td>
                    <td>{when(b.created_at)}</td>
                    <td>
                      <span className={`badge ${cls}`}>{label}</span>
                      {b.rows_undone > 0 && <div className="muted small">{b.rows_undone} undone {b.undone_at ? `on ${day(b.undone_at)}` : ''}</div>}
                    </td>
                    <td className="num">{b.rows_imported}</td>
                    <td className="num">{b.rows_skipped}</td>
                    <td className="num">{b.rows_failed}</td>
                    <td className="num">{money(b.expense_total)}</td>
                    <td className="num">{money(b.income_total)}</td>
                    <td>
                      <div className="actions">
                        <button className="btn ghost small" title="Details" onClick={() => setOpen(b.id)}>
                          <Eye size={14} />
                        </button>
                        {undoable(b) && (
                          <button className="btn ghost small" title="Undo this import" onClick={() => undo(b)}>
                            <Undo2 size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
              {batches && !batches.length && <EmptyRow cols={9}>No imports yet.</EmptyRow>}
              {!batches && <EmptyRow cols={9}>Loading…</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
      {open && <Details id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function Details({ id, onClose }) {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')
  useEffect(() => {
    api.get(`/imports/${id}`).then((r) => setD(r.data), (err) => setError(errorMessage(err)))
  }, [id])
  const rows = (d?.records || []).filter((r) => !filter || r.status === filter)
  return (
    <Modal title={d ? d.filename : 'Import'} subtitle={d ? `Uploaded ${when(d.created_at)}` : ''} onClose={onClose} wide>
      {error && <Msg msg={{ type: 'error', text: error }} />}
      {d && (
        <>
          <div className="segmented" style={{ marginBottom: 12 }}>
            {[['', 'All'], ...Object.entries(ROW_STATUS).map(([k, [, l]]) => [k, l])].map(([k, l]) => {
              const n = k ? d.records.filter((r) => r.status === k).length : d.records.length
              return (n > 0 || !k) && (
                <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
                  {l} <span className="pill">{n}</span>
                </button>
              )
            })}
          </div>
          <div className="table-wrap" style={{ maxHeight: 440 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Date</th>
                  <th>Type</th>
                  <th>Description</th>
                  <th className="num">Amount</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const [cls, label] = ROW_STATUS[r.status] || ['info', r.status]
                  return (
                    <tr key={r.id}>
                      <td className="muted">{r.row_no}</td>
                      <td>{day(r.record_date)}</td>
                      <td>{KIND[r.kind] || '—'}</td>
                      <td>{r.description || <span className="muted">—</span>}</td>
                      <td className="num">{r.amount != null ? money(r.amount) : '—'}</td>
                      <td>
                        <span className={`badge ${cls}`}>{label}</span>
                        {r.reason && <div className="hint" style={{ margin: '4px 0 0' }}>{r.reason}</div>}
                      </td>
                    </tr>
                  )
                })}
                {!rows.length && <EmptyRow cols={6}>No rows.</EmptyRow>}
              </tbody>
            </table>
          </div>
          {d.undo_removes + d.undo_keeps.reduce((s, k) => s + k.rows, 0) > 0 && (
            <p className="hint">
              Undo now would remove {d.undo_removes} record(s)
              {d.undo_keeps.map((k) => `; keep ${k.rows} (${k.reason.toLowerCase()})`).join('')}.
            </p>
          )}
        </>
      )}
    </Modal>
  )
}
