import { Fragment, useEffect, useState } from 'react'
import { Link2, Link2Off, Upload, Wand2, Trash2, FilePlus2 } from 'lucide-react'
import api, { errorMessage, todayISO } from '../api'
import Modal from '../components/Modal'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'
import { Amt, ExportButtons, fmtDate, isCashBank, LedgerOptions, TYPE_LABEL, useBooks, useLoad } from './common'

const FIELDS = [
  ['date_col', 'Date', true],
  ['description_col', 'Description'],
  ['reference_col', 'Cheque / ref no.'],
  ['withdrawal_col', 'Withdrawal (debit)'],
  ['deposit_col', 'Deposit (credit)'],
  ['amount_col', 'Single amount (− or Dr = withdrawal)'],
  ['balance_col', 'Balance'],
]

export default function BankReconciliation() {
  const books = useBooks()
  const { params, settings, meta, ledgers } = books
  const banks = (ledgers.data || []).filter(isCashBank)
  const [ledgerId, setLedgerId] = useState('')
  const [asOf, setAsOf] = useState(todayISO())
  const [msg, setMsg] = useState(null)
  const [matching, setMatching] = useState(null)
  const [booking, setBooking] = useState(null)
  const writable = settings.data?.writable

  useEffect(() => {
    if (!ledgerId && banks.length) setLedgerId(String((banks.find((b) => b.group_code === 'BANK_ACCOUNTS') || banks[0]).id))
  }, [banks.length])

  const ws = useLoad(async () => (ledgerId ? (await api.get('/accounting/bank/workspace', { params: { ...params, ledger_id: ledgerId } })).data : null), [ledgerId, params])
  const brs = useLoad(
    async () => (ledgerId ? (await api.get('/accounting/reports/bank-reconciliation', { params: { ...params, ledger_id: ledgerId, as_of: asOf } })).data : null),
    [ledgerId, params, asOf, ws.data],
  )

  const act = async (fn, text) => {
    try {
      const res = await fn()
      setMsg({ type: 'ok', text: typeof text === 'function' ? text(res.data) : text })
      ws.reload()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const book = ws.data?.book || []
  const bookById = Object.fromEntries(book.map((b) => [b.line_id, b]))

  return (
    <div>
      <PageHeader title="Bank reconciliation" subtitle="Import your bank statement, match it with your books, and reconcile the two balances.">
        <select value={ledgerId} onChange={(e) => setLedgerId(e.target.value)} aria-label="Bank ledger">
          {!banks.length && <option value="">No bank ledgers yet</option>}
          <LedgerOptions ledgers={banks} groups={meta.data?.groups} />
        </select>
      </PageHeader>
      <ReadOnlyNotice />
      <Msg msg={msg} />
      {!banks.length && ledgers.data && (
        <Msg msg={{ type: 'info', text: 'Add your bank accounts under Settings → Bank accounts; each gets a ledger here.' }} />
      )}

      {writable && ledgerId && <ImportCard params={params} ledgerId={ledgerId} onDone={(text) => { setMsg({ type: 'ok', text }); ws.reload() }} onError={(text) => setMsg({ type: 'error', text })} />}

      {ws.data && (
        <div className="card flush">
          <div className="toolbar">
            <h3 style={{ margin: 0 }}>Statement lines</h3>
            <span className="spacer" />
            <span className="muted small">
              {ws.data.statement.filter((s) => s.matched_line_id).length} of {ws.data.statement.length} matched
            </span>
            {writable && (
              <button className="btn secondary small" onClick={() => act(() => api.post('/accounting/bank/auto-match', { ledger_id: Number(ledgerId) }, { params }), (d) => `${d.auto_matched} more line(s) matched`)}>
                <Wand2 size={14} /> Auto-match
              </button>
            )}
          </div>
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th className="num">Withdrawal</th>
                  <th className="num">Deposit</th>
                  <th>In the books</th>
                  {writable && <th></th>}
                </tr>
              </thead>
              <tbody>
                {ws.data.statement.map((s) => {
                  const m = s.matched_line_id && bookById[s.matched_line_id]
                  return (
                    <tr key={s.id}>
                      <td>{fmtDate(s.date)}</td>
                      <td>
                        {s.description}
                        {s.reference && <span className="muted small"> · {s.reference}</span>}
                      </td>
                      <td className="num">{Number(s.withdrawal) ? <Amt v={s.withdrawal} /> : ''}</td>
                      <td className="num">{Number(s.deposit) ? <Amt v={s.deposit} /> : ''}</td>
                      <td>
                        {m ? (
                          <span className="badge ok" title={m.narration || ''}>
                            {TYPE_LABEL[m.voucher_type]} #{m.voucher_no} · {s.match_kind === 'AUTO' ? 'auto' : 'manual'}
                          </span>
                        ) : (
                          <span className="badge warn">Not matched</span>
                        )}
                      </td>
                      {writable && (
                        <td>
                          <div className="actions">
                            {s.matched_line_id ? (
                              <button className="btn ghost small" title="Unmatch" onClick={() => act(() => api.post('/accounting/bank/unmatch', { ledger_id: Number(ledgerId), statement_line_id: s.id }, { params }), 'Unmatched')}>
                                <Link2Off size={14} />
                              </button>
                            ) : (
                              <>
                                <button className="btn ghost small" title="Match with a book entry" onClick={() => setMatching(s)}>
                                  <Link2 size={14} />
                                </button>
                                <button className="btn ghost small" title="Book it (bank charges, interest…)" onClick={() => setBooking(s)}>
                                  <FilePlus2 size={14} />
                                </button>
                              </>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                  )
                })}
                {!ws.data.statement.length && <EmptyRow cols={writable ? 6 : 5}>No statement imported for this ledger yet.</EmptyRow>}
              </tbody>
            </table>
          </div>
          {!!ws.data.imports.length && (
            <div className="toolbar" style={{ borderBottom: 0, borderTop: '1px solid var(--border)' }}>
              <span className="muted small">Imports:</span>
              {ws.data.imports.map((b) => (
                <span key={b.id} className="chip">
                  {b.filename} · {b.line_count} lines
                  {writable && (
                    <button className="btn ghost small" title="Remove this import" onClick={() => window.confirm(`Remove ${b.filename} and its matches?`) && act(() => api.delete(`/accounting/bank/imports/${b.id}`, { params }), 'Import removed')}>
                      <Trash2 size={12} />
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {brs.data && (
        <div className="card">
          <div className="card-head wrap">
            <div>
              <h3>Bank Reconciliation Statement</h3>
              <p>{brs.data.ledger.name} · {brs.data.period}</p>
            </div>
            <div className="filters">
              <label className="filters">
                As at <input type="date" value={asOf} max={todayISO()} onChange={(e) => setAsOf(e.target.value)} />
              </label>
              <ExportButtons url="/accounting/reports/bank-reconciliation" params={{ ...params, ledger_id: ledgerId, as_of: asOf }} name="bank_reconciliation" onError={(text) => setMsg({ type: 'error', text })} />
            </div>
          </div>
          <Brs r={brs.data} />
        </div>
      )}

      {matching && (
        <MatchModal
          line={matching}
          book={book.filter((b) => !b.matched && (Number(matching.deposit) ? Number(b.debit) === Number(matching.deposit) : Number(b.credit) === Number(matching.withdrawal)))}
          allBook={book.filter((b) => !b.matched)}
          onClose={() => setMatching(null)}
          onPick={(b) => {
            setMatching(null)
            act(() => api.post('/accounting/bank/match', { ledger_id: Number(ledgerId), statement_line_id: matching.id, voucher_line_id: b.line_id }, { params }), 'Matched')
          }}
        />
      )}
      {booking && (
        <BookModal
          line={booking}
          ledgers={(ledgers.data || []).filter((g) => g.is_active && !isCashBank(g))}
          groups={meta.data?.groups}
          onClose={() => setBooking(null)}
          onSave={(other, narration) => {
            setBooking(null)
            act(() => api.post('/accounting/bank/voucher', { ledger_id: Number(ledgerId), statement_line_id: booking.id, other_ledger_id: Number(other), narration: narration || null }, { params }),
              (v) => `${TYPE_LABEL[v.voucher_type]} #${v.voucher_no} posted and matched`)
          }}
        />
      )}
    </div>
  )
}

function ImportCard({ params, ledgerId, onDone, onError }) {
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [mapping, setMapping] = useState(null)
  const [busy, setBusy] = useState(false)

  const choose = async (f) => {
    setFile(f)
    setPreview(null)
    if (!f) return
    const form = new FormData()
    form.append('file', f)
    try {
      const res = await api.post('/accounting/bank/preview', form, { params })
      setPreview(res.data)
      setMapping(res.data.guess)
    } catch (err) {
      onError(errorMessage(err))
    }
  }

  const run = async () => {
    setBusy(true)
    const form = new FormData()
    form.append('file', file)
    form.append('ledger_id', ledgerId)
    form.append('mapping', JSON.stringify(mapping))
    try {
      const res = await api.post('/accounting/bank/import', form, { params })
      const d = res.data
      onDone(`${d.added} line(s) imported, ${d.auto_matched} matched automatically${d.duplicates ? `, ${d.duplicates} already imported` : ''}${d.skipped ? `, ${d.skipped} skipped (no date or amount)` : ''}`)
      setFile(null)
      setPreview(null)
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const header = preview ? preview.rows[mapping?.header_row ?? 0] || [] : []
  const colOptions = header.map((h, i) => (
    <option key={i} value={i}>
      {String.fromCharCode(65 + (i % 26))}: {String(h).slice(0, 30) || '(blank)'}
    </option>
  ))
  const setCol = (k) => (e) => setMapping({ ...mapping, [k]: e.target.value === '' ? null : Number(e.target.value) })

  return (
    <div className="card">
      <div className="card-head wrap">
        <div>
          <h3>Import a statement</h3>
          <p>CSV or Excel (.xlsx) from any bank. Check the columns below, then import. Importing the same file twice adds nothing.</p>
        </div>
        <label className="btn secondary">
          <Upload size={16} /> {file ? file.name : 'Choose file'}
          <input type="file" accept=".csv,.txt,.xlsx" hidden onChange={(e) => choose(e.target.files[0] || null)} />
        </label>
      </div>
      {preview && mapping && (
        <div className="stack" style={{ gap: 14 }}>
          <div className="form-grid">
            <div>
              <label>Header row</label>
              <select value={mapping.header_row} onChange={setCol('header_row')}>
                {preview.rows.slice(0, 40).map((r, i) => (
                  <option key={i} value={i}>
                    Row {i + 1}: {r.slice(0, 3).join(' | ').slice(0, 40)}
                  </option>
                ))}
              </select>
            </div>
            {FIELDS.map(([k, label, required]) => (
              <div key={k}>
                <label>{label}</label>
                <select value={mapping[k] ?? ''} onChange={setCol(k)} required={required}>
                  {!required && <option value="">—</option>}
                  {colOptions}
                </select>
              </div>
            ))}
          </div>
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table className="mini-table">
              <tbody>
                {preview.rows.slice(mapping.header_row, mapping.header_row + 8).map((r, i) => (
                  <tr key={i} className={i === 0 ? 'strong-row' : ''}>
                    {r.map((c, j) => (
                      <td key={j}>{String(c)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-actions">
            <span className="muted small grow-space">{preview.total_rows} rows in the file</span>
            <button className="btn" onClick={run} disabled={busy}>
              {busy ? 'Importing…' : 'Import and auto-match'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function MatchModal({ line, book, allBook, onClose, onPick }) {
  const [showAll, setShowAll] = useState(!book.length)
  const list = showAll ? allBook : book
  const amount = Number(line.deposit) || Number(line.withdrawal)
  return (
    <Modal title="Match with a book entry" subtitle={`${fmtDate(line.date)} · ${line.description || ''} · ${Number(line.deposit) ? 'deposit' : 'withdrawal'} ₹${amount.toFixed(2)}`} onClose={onClose} wide>
      <label className="check" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show all unmatched entries (only the same amount and direction can be matched)
      </label>
      <div className="table-wrap" style={{ maxHeight: 380 }}>
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Voucher</th>
              <th>Narration</th>
              <th className="num">In (Dr)</th>
              <th className="num">Out (Cr)</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.map((b) => (
              <tr key={b.line_id}>
                <td>{fmtDate(b.date)}</td>
                <td>{TYPE_LABEL[b.voucher_type]} #{b.voucher_no}</td>
                <td className="muted">{b.narration}</td>
                <td className="num">{Number(b.debit) ? <Amt v={b.debit} /> : ''}</td>
                <td className="num">{Number(b.credit) ? <Amt v={b.credit} /> : ''}</td>
                <td>
                  <button className="btn small" onClick={() => onPick(b)}>Match</button>
                </td>
              </tr>
            ))}
            {!list.length && <EmptyRow cols={6}>No unmatched entry of this amount. Book it as a new voucher instead.</EmptyRow>}
          </tbody>
        </table>
      </div>
    </Modal>
  )
}

function BookModal({ line, ledgers, groups, onClose, onSave }) {
  const [other, setOther] = useState('')
  const [narration, setNarration] = useState(line.description || '')
  const deposit = Number(line.deposit) > 0
  return (
    <Modal title={deposit ? 'Book a receipt from the statement' : 'Book a payment from the statement'} subtitle={`${fmtDate(line.date)} · ₹${(Number(line.deposit) || Number(line.withdrawal)).toFixed(2)}`} onClose={onClose}>
      <div className="form-grid">
        <div className="span-all">
          <label>{deposit ? 'Credit (e.g. Interest Received)' : 'Debit (e.g. Bank Charges)'}</label>
          <select value={other} onChange={(e) => setOther(e.target.value)}>
            <option value="">Choose a ledger…</option>
            <LedgerOptions ledgers={ledgers} groups={groups} />
          </select>
        </div>
        <div className="span-all">
          <label>Narration</label>
          <input value={narration} maxLength={500} onChange={(e) => setNarration(e.target.value)} />
        </div>
      </div>
      <div className="form-actions" style={{ marginTop: 14 }}>
        <button className="btn secondary" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!other} onClick={() => onSave(other, narration)}>Post and match</button>
      </div>
    </Modal>
  )
}

function Brs({ r }) {
  return (
    <div className="table-wrap">
      <table className="table report-table">
        <tbody>
          <tr className="strong-row">
            <td colSpan={2}>Balance as per books</td>
            <td></td>
            <td className="num"><Amt v={r.books_balance} /></td>
          </tr>
          {r.sections.filter((s) => s.items.length).map((s) => (
            <Fragment key={s.title}>
              <tr className="group-row">
                <td colSpan={4}>{s.title}</td>
              </tr>
              {s.items.map((i, k) => (
                <tr key={`${s.title}-${k}`}>
                  <td style={{ paddingLeft: 32 }}>{fmtDate(i.date)}</td>
                  <td>{i.text}</td>
                  <td className="num"><Amt v={i.amount} /></td>
                  <td></td>
                </tr>
              ))}
              <tr>
                <td colSpan={3}></td>
                <td className="num strong"><Amt v={s.total} /></td>
              </tr>
            </Fragment>
          ))}
        </tbody>
        <tfoot>
          <tr className="total-row">
            <td colSpan={3}>Balance as per bank statement</td>
            <td className="num"><Amt v={r.bank_balance} /></td>
          </tr>
          {r.statement_balance != null && (
            <tr>
              <td colSpan={3}>
                Closing balance shown on the statement{' '}
                <span className={`badge ${Number(r.difference) === 0 ? 'ok' : 'bad'}`}>
                  {Number(r.difference) === 0 ? 'Reconciled' : `Difference ₹${Number(r.difference).toFixed(2)}`}
                </span>
              </td>
              <td className="num"><Amt v={r.statement_balance} /></td>
            </tr>
          )}
        </tfoot>
      </table>
      {r.first_statement_date && <p className="hint">Book entries from before {fmtDate(r.first_statement_date)} (the first imported statement line) aren’t checked.</p>}
    </div>
  )
}
