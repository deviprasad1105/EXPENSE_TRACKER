import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { FileUp, History, Keyboard, Plus, RefreshCw, Trash2, Upload } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { Alert, EmptyRow, Msg, PageHeader } from '../components/ui'

export const ACCEPT = '.xlsx,.xls,.csv,.txt,.pdf,.docx,.doc'
const PAGE = 50
const CHUNK = 200 // rows per request: each request stays well inside the server's time limit
const ROLES = [
  ['date', 'Date', true],
  ['description', 'Description / item'],
  ['category', 'Category'],
  ['amount', 'Amount (one column)'],
  ['debit', 'Money out (debit)'],
  ['credit', 'Money in (credit)'],
  ['type', 'Type (expense / income / split)'],
  ['account', 'Account / paid with'],
  ['my_share', 'My share (split bills)'],
  ['split_with', 'Split with (names)'],
  ['quantity', 'Quantity'],
  ['note', 'Note'],
]
export const STATUS = {
  READY: ['ok', 'Ready'],
  INVALID: ['bad', 'Problem'],
  LOCKED: ['bad', 'Locked period'],
  NEEDS_CATEGORY: ['warn', 'Needs a category'],
  NEEDS_ACCOUNT: ['warn', 'Needs an account'],
  DUPLICATE: ['warn', 'Possible duplicate'],
  IMPORTED_BEFORE: ['info', 'Already imported'],
}
const FILTERS = [
  ['all', 'All', () => true],
  ['ready', 'Ready', (r) => r.status === 'READY'],
  ['attention', 'Needs attention', (r) => !r.can_import],
  ['duplicates', 'Duplicates', (r) => r.status === 'DUPLICATE' || r.status === 'IMPORTED_BEFORE'],
  ['out', 'Left out', (r) => r.can_import && !r.include],
]
const col = (i) => (i >= 26 ? String.fromCharCode(64 + Math.floor(i / 26)) : '') + String.fromCharCode(65 + (i % 26))

export function ImportTabs({ active }) {
  const navigate = useNavigate()
  return (
    <div className="segmented">
      <button className={active === 'new' ? 'on' : ''} onClick={() => navigate('/import')}>
        <FileUp size={14} /> New import
      </button>
      <button className={active === 'history' ? 'on' : ''} onClick={() => navigate('/import/history')}>
        <History size={14} /> Import history
      </button>
    </div>
  )
}

export default function ImportRecords() {
  const { target, user } = useAuth()
  const [msg, setMsg] = useState(null)
  const [file, setFile] = useState(null) // what /extract returned
  const [review, setReview] = useState(null) // { filename, file_type, file_hash }
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const viewingOther = target && target.id !== user.id

  const choose = async (input) => {
    const f = input.files[0]
    input.value = '' // so choosing the same file again still triggers a read
    if (!f) return
    setMsg(null)
    setResult(null)
    setReview(null)
    setFile(null)
    setBusy(true)
    const form = new FormData()
    form.append('file', f)
    try {
      const res = await api.post('/imports/extract', form)
      setFile(res.data)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  const startManual = () => {
    setMsg(null)
    setResult(null)
    setFile(null)
    setReview({ filename: 'Typed by hand', file_type: 'manual', file_hash: null, rows: null, mapping: null })
  }

  return (
    <div>
      <PageHeader title="Import past records" subtitle="Bring in expenses and money received from before you used the app: from another app, bank statements or your own sheets.">
        <ImportTabs active="new" />
      </PageHeader>
      {viewingOther && <Alert type="info">Imports always go into your own account, not the data you are viewing.</Alert>}
      <Msg msg={msg} />

      {result ? (
        <Done result={result} onAgain={() => { setResult(null); setFile(null); setReview(null) }} />
      ) : review ? (
        <Review
          source={review}
          onCancel={() => setReview(null)}
          onDone={(r) => { setReview(null); setFile(null); setResult(r) }}
          onError={(text) => setMsg({ type: 'error', text })}
        />
      ) : (
        <>
          <div className="card">
            <div className="card-head wrap">
              <div>
                <h3>1. Upload a file</h3>
                <p>Excel (.xlsx, .xls), CSV, PDF or Word (.docx), up to 5 MB and about 5,000 records. Any column names and date or amount formats: you check what was read before anything is saved.</p>
              </div>
              <div className="filters">
                <button className="btn secondary" onClick={startManual}>
                  <Keyboard size={16} /> Type records instead
                </button>
                <label className="btn">
                  <Upload size={16} /> {busy ? 'Reading…' : file ? file.filename : 'Choose file'}
                  <input type="file" accept={ACCEPT} hidden disabled={busy} onChange={(e) => choose(e.target)} />
                </label>
              </div>
            </div>
            <p className="hint">
              Scanned PDFs and photos of receipts can’t be read yet. Old Word .doc files: save them as .docx or PDF first.
            </p>
          </div>
          {file && <Columns file={file} onRead={(rows, mapping) => setReview({ ...file, rows, mapping })} onError={(text) => setMsg({ type: 'error', text })} />}
        </>
      )}
    </div>
  )
}

function Columns({ file, onRead, onError }) {
  const [tableIndex, setTableIndex] = useState(file.table_index)
  const [mapping, setMapping] = useState(file.guess)
  const table = file.tables[tableIndex]
  const rows = table.rows

  const pickTable = async (i) => {
    setTableIndex(i)
    try {
      const res = await api.post('/imports/guess', { rows: file.tables[i].rows, mapping })
      setMapping(res.data)
    } catch (err) {
      onError(errorMessage(err))
    }
  }

  const width = Math.max(0, ...rows.slice(0, 200).map((r) => r.length))
  const header = mapping.header_row >= 0 ? rows[mapping.header_row] || [] : []
  const colOptions = Array.from({ length: width }, (_, i) => (
    <option key={i} value={i}>
      {col(i)}: {String(header[i] ?? rows[Math.max(0, mapping.header_row + 1)]?.[i] ?? '').slice(0, 28) || '(blank)'}
    </option>
  ))
  const setField = (k) => (e) => setMapping({ ...mapping, [k]: e.target.value === '' ? null : Number(e.target.value) })
  const singleAmount = mapping.amount != null && mapping.debit == null && mapping.credit == null && mapping.type == null
  const used = Object.fromEntries(ROLES.filter(([k]) => mapping[k] != null).map(([k, label]) => [mapping[k], label]))
  const start = Math.max(0, mapping.header_row)
  const missing = mapping.date == null ? 'Choose the date column' : mapping.amount == null && mapping.debit == null && mapping.credit == null ? 'Choose the amount column (or money out / money in)' : null

  return (
    <div className="card">
      <div className="card-head wrap">
        <div>
          <h3>2. Check the columns</h3>
          <p>We guessed what each column holds. Change anything that’s wrong; columns left as “—” are not used.</p>
        </div>
      </div>
      {file.already_imported && (
        <Alert type="warn">
          This file was imported before ({new Date(file.already_imported.date).toLocaleDateString('en-IN')}, {file.already_imported.rows} records). Rows imported then will be marked and left out.
        </Alert>
      )}
      <div className="form-grid">
        {file.tables.length > 1 && (
          <div className="span-all">
            <label>Sheet / table</label>
            <select value={tableIndex} onChange={(e) => pickTable(Number(e.target.value))}>
              {file.tables.map((t, i) => (
                <option key={i} value={i}>
                  {t.name} · {t.rows.length} rows{t.source === 'text' ? ' (read from the text)' : ''}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label>Heading row</label>
          <select value={mapping.header_row} onChange={setField('header_row')}>
            <option value={-1}>No heading row</option>
            {rows.slice(0, 30).map((r, i) => (
              <option key={i} value={i}>
                Row {i + 1}: {r.slice(0, 3).join(' | ').slice(0, 40)}
              </option>
            ))}
          </select>
        </div>
        {ROLES.map(([k, label, required]) => (
          <div key={k}>
            <label>{label}</label>
            <select value={mapping[k] ?? ''} onChange={setField(k)}>
              <option value="">{required ? 'Choose…' : '—'}</option>
              {colOptions}
            </select>
          </div>
        ))}
        <div>
          <label>Dates are written</label>
          <select value={mapping.date_order} onChange={(e) => setMapping({ ...mapping, date_order: e.target.value })}>
            <option value="DMY">Day first (05/11/2026 = 5 Nov)</option>
            <option value="MDY">Month first (11/05/2026 = 5 Nov)</option>
            <option value="YMD">Year first (26/11/05)</option>
          </select>
        </div>
        {singleAmount && (
          <div>
            <label>The amounts are</label>
            <select value={mapping.default_type} onChange={(e) => setMapping({ ...mapping, default_type: e.target.value })}>
              <option value="EXPENSE">All spending (expenses)</option>
              <option value="INCOME">All money received</option>
              <option value="SIGN">Negative = spending, positive = received</option>
            </select>
          </div>
        )}
      </div>
      {table.truncated && <Alert type="warn">This table is longer than the import limit: only its first rows are shown and read.</Alert>}
      <div className="table-wrap" style={{ maxHeight: 260, marginTop: 14 }}>
        <table className="mini-table">
          <thead>
            <tr>
              {Array.from({ length: width }, (_, i) => (
                <th key={i} className={used[i] ? 'strong' : 'muted'}>
                  {col(i)}
                  {used[i] && <div className="small">{used[i]}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(start, start + 9).map((r, i) => (
              <tr key={i} className={i === 0 && mapping.header_row >= 0 ? 'strong-row' : ''}>
                {Array.from({ length: width }, (_, j) => (
                  <td key={j}>{r[j] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-actions" style={{ marginTop: 14 }}>
        <span className="muted small grow-space">{missing || `${rows.length - start - (mapping.header_row >= 0 ? 1 : 0)} rows under the heading`}</span>
        <button className="btn" disabled={!!missing} onClick={() => onRead(rows, mapping)}>
          Read the records
        </button>
      </div>
    </div>
  )
}

function Review({ source, onCancel, onDone, onError }) {
  const [records, setRecords] = useState([])
  const [info, setInfo] = useState(null) // the last check: summary, categories, accounts…
  const [options, setOptions] = useState({ account_mode: 'primary', account_id: null, account_map: {}, category_map: {}, split_group_id: null })
  const [checking, setChecking] = useState(false)
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(0)
  const [progress, setProgress] = useState(null)
  const version = useRef(0)
  const timer = useRef(null)

  const apply = (data) => {
    setInfo(data)
    // A row fixed since the last check (problem → importable) is ticked as a new row would be
    setRecords((prev) =>
      data.records.map((r, i) => (prev[i] && !prev[i].can_import && r.can_import ? { ...r, include: r.default_include } : r)),
    )
  }

  // First read: parse the table (or start an empty list when typing by hand)
  useEffect(() => {
    const run = async () => {
      setChecking(true)
      try {
        const res = source.rows
          ? await api.post('/imports/preview', { rows: source.rows, mapping: source.mapping, options })
          : await api.post('/imports/check', { records: [], options })
        apply(res.data)
        if (!source.rows) addRow([])
      } catch (err) {
        onError(errorMessage(err))
        onCancel()
      } finally {
        setChecking(false)
      }
    }
    run()
  }, [])

  const check = async (recs, opts) => {
    const v = ++version.current
    setChecking(true)
    try {
      const res = await api.post('/imports/check', { records: recs.map(clean), options: opts })
      if (v === version.current) apply(res.data)
    } catch (err) {
      if (v === version.current) onError(errorMessage(err))
    } finally {
      if (v === version.current) setChecking(false)
    }
  }
  const scheduleCheck = (recs, opts = options) => {
    version.current++
    clearTimeout(timer.current)
    timer.current = setTimeout(() => check(recs, opts), 700)
  }
  useEffect(() => () => clearTimeout(timer.current), [])

  const setOption = (patch) => {
    const next = { ...options, ...patch }
    setOptions(next)
    check(records, next)
  }
  const edit = (index, patch) => {
    const next = records.map((r, i) => (i === index ? { ...r, ...patch } : r))
    setRecords(next)
    scheduleCheck(next) // also for a tick: a check already on its way would otherwise undo it
  }
  const addRow = (current = records) => {
    const rowNo = Math.max(0, ...current.map((r) => r.row_no)) + 1
    const next = [...current, { row_no: rowNo, kind: 'EXPENSE', date: todayISO(), description: '', category: null, amount: null, my_share: null, split_with: [], account: null, note: null, quantity: 1, include: true, parse_errors: [], parse_warnings: [], status: 'INVALID', problems: ['Missing amount'], warnings: [], can_import: false }]
    setRecords(next)
    setFilter('all')
    setPage(Math.floor((next.length - 1) / PAGE))
    scheduleCheck(next)
  }
  const removeRow = (index) => {
    const next = records.filter((_, i) => i !== index)
    setRecords(next)
    scheduleCheck(next)
  }

  const shown = useMemo(() => {
    const test = FILTERS.find(([k]) => k === filter)[2]
    return records.map((r, i) => [r, i]).filter(([r]) => test(r))
  }, [records, filter])
  const pages = Math.max(1, Math.ceil(shown.length / PAGE))
  const chosen = records.filter((r) => r.can_import && r.include)
  const problems = records.filter((r) => !r.can_import).length
  const totalOut = chosen.filter((r) => r.kind !== 'INCOME').reduce((s, r) => s + Number(r.amount || 0), 0)
  const totalIn = chosen.filter((r) => r.kind === 'INCOME').reduce((s, r) => s + Number(r.amount || 0), 0)
  const hasSplit = records.some((r) => r.kind === 'SPLIT')
  const hasAccountColumn = records.some((r) => r.account)
  const s = info?.summary

  const runImport = async () => {
    const left = records.length - chosen.length
    if (!window.confirm(`Import ${chosen.length} record(s)?${left ? `\n\n${left} other row(s) will be listed in the import history as skipped or failed, with the reason.` : ''}`)) return
    setProgress({ done: 0, total: records.length })
    try {
      const batch = (await api.post('/imports', { filename: source.filename, file_type: source.file_type, file_hash: source.file_hash })).data
      for (let i = 0; i < records.length; i += CHUNK) {
        await api.post(`/imports/${batch.id}/rows`, { records: records.slice(i, i + CHUNK).map(clean), options })
        setProgress({ done: Math.min(records.length, i + CHUNK), total: records.length })
      }
      const done = (await api.post(`/imports/${batch.id}/finish`)).data
      onDone(done)
    } catch (err) {
      onError(`${errorMessage(err)}. What was imported before the error is in Import history, where it can be undone.`)
      setProgress(null)
    }
  }

  if (!info) return <div className="card muted">Reading the records…</div>

  return (
    <>
      <div className="card">
        <div className="card-head wrap">
          <div>
            <h3>3. Review and correct</h3>
            <p>
              {source.filename} · {records.length} record(s)
              {info.ignored_rows ? ` · ${info.ignored_rows} blank or heading row(s) skipped` : ''}. Fix a row by editing it; untick what you don’t want.
            </p>
          </div>
          <div className="filters">
            {checking && <span className="muted small"><RefreshCw size={14} className="spin" /> Checking…</span>}
            <button className="btn secondary" onClick={onCancel}>Start over</button>
          </div>
        </div>

        <div className="form-grid">
          <div>
            <label>Account for these records</label>
            <select
              value={options.account_mode === 'fixed' ? `id:${options.account_id}` : options.account_mode}
              onChange={(e) => {
                const v = e.target.value
                setOption(v.startsWith('id:') ? { account_mode: 'fixed', account_id: Number(v.slice(3)) } : { account_mode: v, account_id: null })
              }}
            >
              <option value="primary">The primary account (app default)</option>
              <option value="none">No account (unassigned)</option>
              {info.accounts.map((a) => (
                <option key={a.id} value={`id:${a.id}`}>
                  {a.name}{a.last4 ? ` ••${a.last4}` : ''}
                </option>
              ))}
              {hasAccountColumn && <option value="column">From the file’s account column</option>}
            </select>
          </div>
          {hasSplit && (
            <div>
              <label>Split bills go to the group</label>
              <select value={options.split_group_id ?? ''} onChange={(e) => setOption({ split_group_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">New group: “Imported split bills”</option>
                {info.split_groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
        {hasSplit && (
          <p className="hint">
            A split row adds your share as an expense; what you paid for the others becomes a split bill they owe you (shared between the names in the row, or “Others”).
          </p>
        )}
        {info.lock_date && <p className="hint">Your accounting books are locked up to {new Date(info.lock_date).toLocaleDateString('en-IN')}: records dated on or before it can’t be imported.</p>}

        <CategoryMap info={info} options={options} setOption={setOption} />
        {options.account_mode === 'column' && <AccountMap info={info} options={options} setOption={setOption} />}
      </div>

      <div className="card flush">
        <div className="toolbar wrap">
          <div className="segmented">
            {FILTERS.map(([k, label, test]) => (
              <button key={k} className={filter === k ? 'on' : ''} onClick={() => { setFilter(k); setPage(0) }}>
                {label} <span className="pill">{records.filter(test).length}</span>
              </button>
            ))}
          </div>
          <span className="spacer" />
          <button className="btn secondary small" onClick={() => addRow()}>
            <Plus size={14} /> Add a record
          </button>
        </div>
        <div className="table-wrap">
          <table className="table import-table">
            <thead>
              <tr>
                <th title="Import this row"></th>
                <th>Row</th>
                <th>Status</th>
                <th>Date</th>
                <th>Type</th>
                <th>Description</th>
                <th>Category / source</th>
                <th className="num">Amount</th>
                {hasSplit && <th className="num">My share</th>}
                {options.account_mode === 'column' && <th>Account</th>}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(page * PAGE, page * PAGE + PAGE).map(([r, i]) => (
                <Row key={`${r.row_no}-${i}`} r={r} hasSplit={hasSplit} accountColumn={options.account_mode === 'column'}
                     onEdit={(patch) => edit(i, patch)} onRemove={() => removeRow(i)} />
              ))}
              {!shown.length && <EmptyRow cols={11}>No records here.</EmptyRow>}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="toolbar" style={{ borderTop: '1px solid var(--border)', borderBottom: 0 }}>
            <button className="btn secondary small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="muted small">Page {page + 1} of {pages}</span>
            <button className="btn secondary small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>

      <div className="card">
        <div className="form-actions">
          <div className="grow-space">
            <div className="strong">{chosen.length} record(s) to import</div>
            <div className="muted small">
              Spending {money(totalOut)} · Money received {money(totalIn)}
              {s && (s.duplicates || s.imported_before) ? ` · ${s.duplicates + s.imported_before} duplicate(s) left out unless ticked` : ''}
              {problems ? ` · ${problems} row(s) with problems won’t be imported` : ''}
            </div>
          </div>
          {progress ? (
            <div style={{ minWidth: 220 }}>
              <div className="progress"><i style={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%`, background: 'var(--primary)' }} /></div>
              <div className="muted small">Importing… {progress.done} of {progress.total}</div>
            </div>
          ) : (
            <button className="btn" disabled={checking || !chosen.length} onClick={runImport}>
              Import {chosen.length} record(s)
            </button>
          )}
        </div>
        <p className="hint">
          Records keep their original date and appear in Expenses, Balance, the Dashboard and Reports. They can be edited for 3 days from today, don’t trigger budget alerts, and post to your books if you use Accounting. You can undo the whole import from Import history.
        </p>
      </div>
    </>
  )
}

function Row({ r, hasSplit, accountColumn, onEdit, onRemove }) {
  const [cls, label] = STATUS[r.status] || ['info', r.status]
  const notes = [...(r.problems || []), ...(r.warnings || [])]
  const num = (v) => (v === '' ? null : v)
  return (
    <tr className={r.can_import ? '' : 'row-problem'}>
      <td>
        <input type="checkbox" aria-label={`Import row ${r.row_no}`} checked={!!(r.can_import && r.include)} disabled={!r.can_import}
               onChange={(e) => onEdit({ include: e.target.checked })} />
      </td>
      <td className="muted">{r.row_no}</td>
      <td style={{ minWidth: 150 }}>
        <span className={`badge ${cls}`}>{label}</span>
        {notes.map((n, k) => (
          <div key={k} className="hint" style={{ margin: '4px 0 0' }}>{n}</div>
        ))}
      </td>
      <td>
        <input type="date" value={r.date || ''} max={todayISO()} onChange={(e) => onEdit({ date: e.target.value || null })} />
      </td>
      <td>
        <select value={r.kind || ''} onChange={(e) => onEdit({ kind: e.target.value || null })}>
          <option value="">Choose…</option>
          <option value="EXPENSE">Expense</option>
          <option value="INCOME">Income</option>
          <option value="SPLIT">Split bill</option>
        </select>
      </td>
      <td>
        <input value={r.description || ''} maxLength={300} onChange={(e) => onEdit({ description: e.target.value })} />
      </td>
      <td>
        <input value={r.category || ''} maxLength={100} placeholder={r.kind === 'INCOME' ? 'Income' : ''} onChange={(e) => onEdit({ category: e.target.value || null })} />
        {r.category_final && r.category_final !== r.category && <div className="hint" style={{ margin: '4px 0 0' }}>→ {r.category_final}</div>}
      </td>
      <td className="num">
        <input type="number" min="0" step="0.01" value={r.amount ?? ''} style={{ width: 120 }} onChange={(e) => onEdit({ amount: num(e.target.value) })} />
      </td>
      {hasSplit && (
        <td className="num">
          {r.kind === 'SPLIT' && (
            <>
              <input type="number" min="0" step="0.01" value={r.my_share ?? ''} style={{ width: 110 }} onChange={(e) => onEdit({ my_share: num(e.target.value) })} />
              <div className="hint" style={{ margin: '4px 0 0' }}>with {(r.split_with || []).join(', ') || 'Others'}</div>
            </>
          )}
        </td>
      )}
      {accountColumn && (
        <td>
          <input value={r.account || ''} maxLength={100} onChange={(e) => onEdit({ account: e.target.value || null })} />
        </td>
      )}
      <td>
        <button className="btn ghost small" title="Remove this row from the import" onClick={onRemove}>
          <Trash2 size={14} />
        </button>
      </td>
    </tr>
  )
}

function CategoryMap({ info, options, setOption }) {
  const unknown = info.category_values.filter((c) => !c.known)
  if (!unknown.length) return null
  const choose = (value, choice) => {
    let target = choice
    if (choice === '__new__') {
      target = window.prompt('Name of the new category', value)?.trim()
      if (!target) return
    }
    const map = { ...options.category_map }
    if (target) map[value.toLowerCase()] = target
    else delete map[value.toLowerCase()]
    setOption({ category_map: map })
  }
  return (
    <div style={{ marginTop: 16 }}>
      <h4 style={{ margin: '0 0 4px' }}>Categories not in the app yet</h4>
      <p className="hint" style={{ marginTop: 0 }}>Choose an existing category for each, or create it. Rows stay unimported until their category is chosen.</p>
      <div className="form-grid">
        {unknown.map((c) => {
          const mapped = options.category_map[c.value.toLowerCase()]
          const isNew = mapped && !info.categories.includes(mapped)
          return (
            <div key={c.value}>
              <label>{c.value ? `“${c.value}”` : 'Rows without a category'} · {c.rows} row(s)</label>
              <select value={mapped ? (isNew ? `new:${mapped}` : mapped) : ''} onChange={(e) => choose(c.value, e.target.value.startsWith('new:') ? e.target.value.slice(4) : e.target.value)}>
                <option value="">Choose…</option>
                {c.value && <option value={`new:${c.value}`}>Create “{c.value}” as a new category</option>}
                {isNew && mapped !== c.value && <option value={`new:${mapped}`}>New category “{mapped}”</option>}
                <option value="__new__">Create a new category with another name…</option>
                <optgroup label="Your categories">
                  {info.categories.map((k) => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                </optgroup>
              </select>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AccountMap({ info, options, setOption }) {
  if (!info.account_values.length) return null
  const choose = (value, choice) => {
    const map = { ...options.account_map }
    if (choice === '') delete map[value.toLowerCase()]
    else map[value.toLowerCase()] = choice === 'none' ? null : Number(choice)
    setOption({ account_map: map })
  }
  return (
    <div style={{ marginTop: 16 }}>
      <h4 style={{ margin: '0 0 4px' }}>Accounts named in the file</h4>
      <p className="hint" style={{ marginTop: 0 }}>Matched by name or last 4 digits where possible. Choose the account for the others, or leave them unassigned.</p>
      <div className="form-grid">
        {info.account_values.map((a) => {
          const key = a.value.toLowerCase()
          const value = key in options.account_map ? (options.account_map[key] === null ? 'none' : String(options.account_map[key])) : ''
          return (
            <div key={a.value}>
              <label>{a.value ? `“${a.value}”` : 'Rows without an account'} · {a.rows} row(s)</label>
              <select value={value} onChange={(e) => choose(a.value, e.target.value)}>
                <option value="">{a.account_id ? `Matched: ${info.accounts.find((x) => x.id === a.account_id)?.name || 'an account'}` : 'Choose…'}</option>
                <option value="none">No account (unassigned)</option>
                {info.accounts.map((x) => (
                  <option key={x.id} value={x.id}>{x.name}{x.last4 ? ` ••${x.last4}` : ''}</option>
                ))}
              </select>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Done({ result, onAgain }) {
  const ok = result.status === 'COMPLETED'
  return (
    <div className="card">
      <Alert type={ok ? 'ok' : result.rows_imported ? 'warn' : 'error'}>
        {result.rows_imported} record(s) imported from {result.filename}
        {result.rows_skipped ? `, ${result.rows_skipped} left out` : ''}
        {result.rows_failed ? `, ${result.rows_failed} failed` : ''}.
      </Alert>
      <p>
        Spending {money(result.expense_total)} · Money received {money(result.income_total)}
      </p>
      <div className="form-actions">
        <Link className="btn secondary" to="/expenses">Open Expenses</Link>
        <Link className="btn secondary" to="/import/history">See it in Import history</Link>
        <button className="btn" onClick={onAgain}>Import another file</button>
      </div>
    </div>
  )
}

// What the server needs of a row (drops the check's own fields)
function clean(r) {
  return {
    row_no: r.row_no, kind: r.kind || null, date: r.date || null, description: r.description || '', category: r.category || null,
    amount: r.amount === '' ? null : r.amount, my_share: r.kind === 'SPLIT' ? (r.my_share === '' ? null : r.my_share) : null,
    split_with: r.split_with || [], account: r.account || null, note: r.note || null, quantity: r.quantity || 1,
    include: r.include !== false, parse_errors: r.parse_errors || [], parse_warnings: r.parse_warnings || [], fingerprint: r.fingerprint || null,
  }
}
