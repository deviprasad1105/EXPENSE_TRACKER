import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, Download, FileUp, History, Keyboard, Plus, RefreshCw, Sparkles, Tag, Trash2, Upload } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { ACCOUNT_KINDS } from '../components/Accounts'
import { Alert, EmptyRow, Msg, PageHeader } from '../components/ui'

export const ACCEPT = '.xlsx,.xls,.csv,.txt,.pdf,.docx,.doc'
const PAGE = 50
const CHUNK = 200 // rows per request: each request stays well inside the server's time limit
const MAX_UPLOAD = 4_400_000 // the server takes at most 4.5 MB per request
const MAX_FILE = 5_000_000

// A bigger file is gzipped before uploading (CSV and text shrink a lot; the server unpacks it)
async function uploadable(f) {
  if (f.size > MAX_FILE) throw new Error('The file is too large: 5 MB at most.')
  if (f.size <= 1_000_000 || typeof CompressionStream === 'undefined') {
    if (f.size > MAX_UPLOAD) throw new Error("This browser can't compress the file, and it is over 4.4 MB. Use a newer browser, or split the file.")
    return [f, f.name]
  }
  const packed = await new Response(f.stream().pipeThrough(new CompressionStream('gzip'))).blob()
  if (packed.size > MAX_UPLOAD) throw new Error('This file is too big to upload, even compressed. Split it into smaller files, or save it as CSV.')
  return [packed, `${f.name}.gz`]
}
const ROLES = [
  ['date', 'Date', true],
  ['time', 'Time (if in its own column)'],
  ['description', 'Description / item'],
  ['category', 'Category'],
  ['amount', 'Amount (one column)'],
  ['debit', 'Money out (debit)'],
  ['credit', 'Money in (credit)'],
  ['type', 'Type (expense / income / split)'],
  ['account', 'Account / paid with (or “From→To”)'],
  ['to_account', 'To account (transfers)'],
  ['my_share', 'My share (split bills)'],
  ['paid_by', 'Paid by (split bills; blank = you)'],
  ['split_with', 'Split with (names)'],
  ['quantity', 'Quantity'],
  ['note', 'Note'],
]
export const STATUS = {
  READY: ['ok', 'Ready'],
  INVALID: ['bad', 'Problem in the file'],
  LOCKED: ['bad', 'Locked period'],
  NEEDS_CATEGORY: ['warn', 'Choose a category'],
  NEEDS_ACCOUNT: ['warn', 'Choose an account'],
  DUPLICATE: ['bad', 'Already in the app'],
  IMPORTED_BEFORE: ['bad', 'Already imported'],
  REPEATED: ['bad', 'Repeated in the file'],
  IGNORED: ['info', 'Not a transaction'],
}
// Problems in the file: any of them blocks the whole file
const BLOCKING = ['INVALID', 'LOCKED', 'DUPLICATE', 'IMPORTED_BEFORE', 'REPEATED']
// [key, label, test, shown even when empty]
const FILTERS = [
  ['all', 'All', () => true, true],
  ['ready', 'Ready', (r) => r.status === 'READY' && r.can_import, true],
  ['problems', 'Problems in the file', (r) => BLOCKING.includes(r.status)],
  ['choose', 'To choose', (r) => r.status === 'NEEDS_CATEGORY' || r.status === 'NEEDS_ACCOUNT'],
  ['check', 'To check', (r) => r.to_check],
  ['transfers', 'Transfers', (r) => r.effective_kind === 'TRANSFER' || r.kind === 'OPENING', true],
  ['ignored', 'Not transactions', (r) => r.status === 'IGNORED'],
]

/** Remove an import entirely, a few hundred records per request (Purge & re-import, or taking back a stopped import). */
export async function purgeAll(batchId, onProgress) {
  let removed = 0
  for (;;) {
    const res = (await api.post(`/imports/${batchId}/purge`)).data
    removed += res.removed
    if (!res.remaining) return { ...res, removed }
    onProgress?.(res.remaining)
  }
}

// The rows with problems, as a CSV to fix the file with
function problemsCsv(filename, rows) {
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = [['Row', 'Date', 'Description', 'Amount', 'Problem'].map(q).join(',')]
  for (const r of rows) lines.push([r.row_no, r.date, r.description, r.amount, (r.problems || []).join('; ')].map(q).join(','))
  const url = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${filename.replace(/\.[^.]+$/, '')} - problems.csv`
  a.click()
  URL.revokeObjectURL(url)
}
const KIND_NAME = { WALLET: 'wallet', CASH: 'cash account', SAVINGS: 'bank account', CURRENT: 'bank account', SALARY: 'bank account' }

// Accounts (bank, cash, wallet) that would end below zero: some of their rows are probably missing from the file
function BelowZero({ warnings }) {
  if (!warnings?.length) return null
  return (
    <Alert type="warn">
      {warnings.map((w) => (
        <div key={w.account}>
          “{w.account}” would end at {money(w.balance)}. A {KIND_NAME[w.kind] || 'account'} can’t go below zero, so some of its
          {w.kind === 'WALLET' ? ' top-ups' : ' money in'} are probably missing from this file. Import that {KIND_NAME[w.kind] || 'account'}’s own
          statement (PDF, Excel or CSV) as well to complete it.
        </div>
      ))}
    </Alert>
  )
}
export const KIND_LABEL = { EXPENSE: 'Expense', INCOME: 'Income', SPLIT: 'Split bill', TRANSFER: 'Transfer', OPENING: 'Opening balance' }
// What a category can be moved to instead of counting as spending / income
const TREAT_NEW = [
  ['new:INVESTMENT:Investments', 'Investments (new account)'],
  ['new:RECEIVABLE:Money with others', 'Money with others (new account)'],
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
  // Purge & re-import: the earlier import the next file replaces (from Import history, or offered after an upload)
  const [params, setParams] = useSearchParams()
  const replaceId = Number(params.get('replace')) || null
  const setReplace = (id) => setParams(id ? { replace: String(id) } : {})

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
    try {
      const [body, name] = await uploadable(f)
      form.append('file', body, name)
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
      {replaceId && !result && (
        <Alert type="info">
          Purge &amp; re-import: the file you choose replaces an earlier import. Once every row of it is checked and imported,
          that import’s records are removed (also those edited since). If the file has a problem, nothing changes.{' '}
          <button className="btn ghost small" onClick={() => setReplace(null)}>Cancel</button>
        </Alert>
      )}

      {result ? (
        <Done result={result} onAgain={() => { setResult(null); setFile(null); setReview(null); setReplace(null) }} />
      ) : review ? (
        <Review
          key={`${replaceId || 0}`}
          source={review}
          replaceId={replaceId}
          onReplace={setReplace}
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
                <p>Excel (.xlsx, .xls), CSV, PDF or Word (.docx), up to 5 MB (bigger files are compressed before uploading) and about 5,000 records. Any column names and date or amount formats: you check what was read before anything is saved.</p>
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
          {file?.already_imported && !replaceId && (
            <Alert type="warn">
              This file was imported on {new Date(file.already_imported.date).toLocaleDateString('en-IN')} as “{file.already_imported.filename}”
              ({file.already_imported.rows} records), so it can’t be imported again. To correct that import, replace it with this file:{' '}
              <button className="btn small" onClick={() => setReplace(file.already_imported.batch_id)}>Purge &amp; re-import</button>
            </Alert>
          )}
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
      {table.truncated && (
        <Alert type="error">
          This table has {(table.total_rows || rows.length).toLocaleString('en-IN')} rows; up to about 5,000 records can be imported at once.
          Split it into smaller files (for example one per year) and import them one after another.
        </Alert>
      )}
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
        <button className="btn" disabled={!!missing || table.truncated} onClick={() => onRead(rows, mapping)}>
          Read the records
        </button>
      </div>
    </div>
  )
}

function Review({ source, replaceId, onReplace, onCancel, onDone, onError }) {
  const fromFile = !!source.rows // rows from a file can't be changed here: the file is fixed and uploaded again
  const [records, setRecords] = useState([])
  const [info, setInfo] = useState(null) // the last check: summary, categories, accounts…
  // A file with an account column uses it (transfers and opening balances name their accounts anyway)
  const [options, setOptions] = useState({
    account_mode: source.mapping?.account != null ? 'column' : 'primary', account_id: null, account_map: {}, create_accounts: {},
    category_map: {}, category_treatment: {}, split_group_id: null,
  })
  const [checking, setChecking] = useState(false)
  const [remembered, setRemembered] = useState(null) // what was filled in from the last import
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(0)
  const [progress, setProgress] = useState(null)
  // Alias names / categories typed in "Payees in this file", not saved yet: { payee key: { alias, category } }
  const [drafts, setDrafts] = useState({})
  const [renameOld, setRenameOld] = useState(true)
  const version = useRef(0)
  const timer = useRef(null)

  const apply = (data) => {
    setInfo(data)
    setRecords(data.records)
  }

  // First read: parse the table (or start an empty list when typing by hand)
  useEffect(() => {
    const run = async () => {
      setChecking(true)
      try {
        const res = source.rows
          ? await api.post('/imports/preview', { rows: source.rows, mapping: source.mapping, options, replaces_id: replaceId })
          : await api.post('/imports/check', { records: [], options })
        apply(res.data)
        if (!source.rows) addRow([])
        // Choices made in an earlier import for the same categories and accounts are filled in again
        const rem = res.data.remembered || {}
        if (Object.keys(rem).length) {
          const next = {
            ...options,
            category_map: { ...rem.category_map, ...options.category_map },
            category_treatment: { ...rem.category_treatment, ...options.category_treatment },
            account_map: { ...rem.account_map, ...options.account_map },
          }
          setOptions(next)
          setRemembered({
            categories: Object.keys(rem.category_map || {}).length,
            moves: Object.keys(rem.category_treatment || {}).length,
            accounts: Object.keys(rem.account_map || {}).length,
          })
          const again = await api.post('/imports/check', { records: res.data.records.map(clean), options: next, replaces_id: replaceId })
          apply(again.data)
        }
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
      const res = await api.post('/imports/check', { records: recs.map(clean), options: opts, replaces_id: replaceId })
      if (v === version.current) apply(res.data)
      return res.data
    } catch (err) {
      if (v === version.current) onError(errorMessage(err))
    } finally {
      if (v === version.current) setChecking(false)
    }
  }

  // Changed payee names: a new alias, or a new name / category for an existing one
  const pending = (info?.payees || []).filter((p) => {
    const d = drafts[p.payee_key]
    if (!d) return false
    const name = (d.alias ?? '').trim()
    const cat = (d.category ?? '').trim()
    return p.alias_id ? (name && name !== p.alias) || cat !== (p.alias_category || '') : !!name
  })

  /** Save the payee names, then check again so the rows show them (and their categories). Returns the new records. */
  const saveAliases = async () => {
    if (!pending.length) return records
    setChecking(true)
    let saved = 0
    try {
      for (const p of pending) {
        const d = drafts[p.payee_key]
        const name = (d.alias ?? p.alias ?? '').trim()
        const category = (d.category ?? p.alias_category ?? '').trim() || null
        if (p.alias_id) await api.put(`/aliases/${p.alias_id}`, { alias: name, category, clear_category: !category })
        else
          await api.post('/aliases', { match_key: p.payee_key, alias: name, category, apply_all_existing: renameOld, set_category_existing: renameOld && !!category })
        saved++
      }
    } catch (err) {
      onError(`${errorMessage(err)} (${saved} of ${pending.length} payee name(s) saved)`)
    }
    setDrafts({})
    const data = await check(records, options)
    return data ? data.records : null
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
    const next = [...current, { row_no: rowNo, kind: 'EXPENSE', effective_kind: 'EXPENSE', date: todayISO(), description: '', category: null, amount: null, my_share: null, split_with: [], account: null, to_account: null, note: null, quantity: 1, include: true, parse_errors: [], parse_warnings: [], status: 'INVALID', problems: ['Missing amount'], warnings: [], can_import: false }]
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
  const chosen = records.filter((r) => r.can_import)
  const fileProblems = records.filter((r) => BLOCKING.includes(r.status))
  const toChoose = records.filter((r) => r.status === 'NEEDS_CATEGORY' || r.status === 'NEEDS_ACCOUNT').length
  const sumOf = (kinds) => chosen.filter((r) => kinds.includes(r.effective_kind)).reduce((s, r) => s + Number(r.amount || 0), 0)
  const totalOut = sumOf(['EXPENSE', 'SPLIT'])
  const totalIn = sumOf(['INCOME'])
  const totalMoved = sumOf(['TRANSFER'])
  const transfers = chosen.filter((r) => r.effective_kind === 'TRANSFER').length
  const openings = chosen.filter((r) => r.kind === 'OPENING').length
  const hasSplit = records.some((r) => r.kind === 'SPLIT')
  const hasAccountColumn = records.some((r) => r.account)
  const s = info?.summary

  const runImport = async () => {
    let recs = records
    if (pending.length) {
      if (!window.confirm(`Save the ${pending.length} payee name(s) you typed first, so these records get them?`)) return
      recs = await saveAliases()
      if (!recs) return
    }
    const picked = recs.filter((r) => r.can_import)
    const ignored = recs.filter((r) => r.status === 'IGNORED').length
    const old = info.replaces
    const replacing = old
      ? `\n\nThen “${old.filename}” is removed: ${old.removes} record(s)${old.edited ? `, including ${old.edited} edited since its import` : ''}.`
      : ''
    if (!window.confirm(`Import all ${picked.length} record(s) of ${source.filename}?${ignored ? `\n\n${ignored} balance / total line(s) aren’t transactions and are left out.` : ''}${replacing}\n\nIf anything stops the import, what was saved is taken back: the file comes in whole or not at all.`)) return
    setProgress({ done: 0, total: recs.length, label: 'Importing…' })
    let batch = null
    try {
      batch = (await api.post('/imports', {
        filename: source.filename, file_type: source.file_type, file_hash: source.file_hash,
        records: recs.map(clean), options, replaces_id: replaceId,
      })).data
      const created = []
      for (let i = 0; i < recs.length; i += CHUNK) {
        const res = await api.post(`/imports/${batch.id}/rows`, { records: recs.slice(i, i + CHUNK).map(clean), options })
        created.push(...(res.data.accounts_created || []))
        setProgress({ done: Math.min(recs.length, i + CHUNK), total: recs.length, label: 'Importing…' })
      }
      const done = (await api.post(`/imports/${batch.id}/finish`)).data
      batch = null // in: nothing to take back from here on
      let replaced = null
      if (old) {
        try {
          replaced = await purgeAll(old.id, (n) => setProgress({ done: old.removes - n, total: old.removes, label: `Removing “${old.filename}”…` }))
          replaced = { ...replaced, filename: old.filename }
        } catch (err) {
          replaced = { error: errorMessage(err), filename: old.filename }
        }
      }
      onReplace(null)
      onDone({ ...done, accounts_created: created, account_warnings: s?.account_warnings, replaced })
    } catch (err) {
      let why = errorMessage(err)
      if (batch) {
        // Take back what was saved: the file comes in whole or not at all
        try {
          await purgeAll(batch.id, (n) => setProgress({ done: n, total: recs.length, label: 'Taking it back…' }))
          why += '. Nothing from this file was kept: try again'
        } catch {
          why += '. What was saved before the error couldn’t all be taken back: open Import history and press “Take back” on it'
        }
      }
      onError(why)
      setProgress(null)
    }
  }

  if (!info) return <div className="card muted">Reading the records…</div>

  return (
    <>
      <div className="card">
        <div className="card-head wrap">
          <div>
            <h3>3. Review</h3>
            <p>
              {source.filename} · {records.length} record(s)
              {info.ignored_rows ? ` · ${info.ignored_rows} blank or heading row(s) skipped` : ''}.{' '}
              {fromFile
                ? 'The whole file is imported, or nothing: every row must be right. Choose categories and accounts below; anything else is fixed in the file, which you then upload again.'
                : 'Add and correct the records; all of them are imported together.'}
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
        {remembered && (
          <Alert type="info">
            Filled in from your earlier imports:{' '}
            {[
              remembered.categories && `${remembered.categories} category choice(s)`,
              remembered.moves && `${remembered.moves} category move(s)`,
              remembered.accounts && `${remembered.accounts} account(s)`,
            ].filter(Boolean).join(', ')}. Change any of them below.
          </Alert>
        )}
        {info.books_moves_to && (
          <Alert type="info">
            {info.before_books} record(s) are older than your accounting books, which start on{' '}
            {new Date(info.books_start).toLocaleDateString('en-IN')}. The books will start from{' '}
            {new Date(info.books_moves_to).toLocaleDateString('en-IN')} instead, so every record is posted and nothing is lost.
          </Alert>
        )}
        {info.replaces && (
          <Alert type={info.replaces.blocks.length ? 'error' : 'info'}>
            This file replaces “{info.replaces.filename}”, imported on {new Date(info.replaces.created_at).toLocaleDateString('en-IN')}:
            its {info.replaces.removes} record(s){info.replaces.edited ? `, including ${info.replaces.edited} edited since` : ''} are removed once this file is in.
            {info.replaces.blocks.length > 0 && ` It can’t be purged: ${info.replaces.blocks.map((b) => `${b.rows} record(s): ${b.reason}`).join('; ')}. Unlock the period or unmatch the bank statement lines first.`}
          </Alert>
        )}
        {info.blocked && (
          <Alert type="error">
            <div className="strong">This file can’t be imported: {fileProblems.length} row(s) have a problem in the file.</div>
            Nothing is imported until every row is right, so a file never comes in half or twice. Fix these rows in the file and upload it again:
            <ul style={{ margin: '6px 0' }}>
              {fileProblems.slice(0, 8).map((r) => (
                <li key={r.row_no}>Row {r.row_no}: {r.problems?.[0]}</li>
              ))}
              {fileProblems.length > 8 && <li>…and {fileProblems.length - 8} more (see “Problems in the file” below)</li>}
            </ul>
            <div className="filters">
              <button className="btn secondary small" onClick={() => problemsCsv(source.filename, fileProblems)}>
                <Download size={14} /> Download the list
              </button>
              {!replaceId && (info.earlier_imports || []).map((b) => (
                <button key={b.id} className="btn small" onClick={() => onReplace(b.id)}>
                  Purge &amp; re-import “{b.filename}” with this file
                </button>
              ))}
            </div>
          </Alert>
        )}
        {info.lock_date && <p className="hint">Your accounting books are locked up to {new Date(info.lock_date).toLocaleDateString('en-IN')}: records dated on or before it can’t be imported.</p>}

        <AccountMap info={info} options={options} setOption={setOption} />
        <PayeeMap
          info={info} drafts={drafts} setDrafts={setDrafts} pending={pending} onSave={saveAliases} busy={checking}
          renameOld={renameOld} setRenameOld={setRenameOld}
        />
        <CategoryMap info={info} options={options} setOption={setOption} />
        <TreatmentMap info={info} options={options} setOption={setOption} />
      </div>

      <div className="card flush">
        <div className="toolbar wrap">
          <div className="segmented">
            {FILTERS.map(([k, label, test, always]) => {
              const n = records.filter(test).length
              if (!always && !n && filter !== k) return null
              return (
                <button key={k} className={filter === k ? 'on' : ''} onClick={() => { setFilter(k); setPage(0) }}>
                  {label} <span className="pill">{n}</span>
                </button>
              )
            })}
          </div>
          <span className="spacer" />
          {!fromFile && (
            <button className="btn secondary small" onClick={() => addRow()}>
              <Plus size={14} /> Add a record
            </button>
          )}
        </div>
        <div className="table-wrap">
          <table className="table import-table">
            <thead>
              <tr>
                <th>Row</th>
                <th>Status</th>
                <th>Date</th>
                <th>Type</th>
                <th>Description</th>
                <th>Category / source</th>
                <th className="num">Amount</th>
                {hasSplit && <th className="num">My share</th>}
                <th>Account</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(page * PAGE, page * PAGE + PAGE).map(([r, i]) => (
                <Row key={`${r.row_no}-${i}`} r={r} hasSplit={hasSplit} readOnly={fromFile}
                     onEdit={(patch) => edit(i, patch)} onRemove={() => removeRow(i)} />
              ))}
              {!shown.length && <EmptyRow cols={10}>No records here.</EmptyRow>}
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
        <BelowZero warnings={s?.account_warnings} />
        <div className="form-actions">
          <div className="grow-space">
            <div className="strong">{chosen.length} record(s) to import</div>
            <div className="muted small">
              Spending {money(totalOut)} · Money received {money(totalIn)}
              {transfers ? ` · ${transfers} transfer(s) of ${money(totalMoved)}` : ''}
              {openings ? ` · ${openings} opening balance(s)` : ''}
              {s?.ignored ? ` · ${s.ignored} balance / total line(s) left out` : ''}
              {s?.to_check ? ` · ${s.to_check} unusually large row(s) to check` : ''}
              {fileProblems.length ? ` · ${fileProblems.length} row(s) with a problem in the file: fix the file and upload it again` : ''}
              {toChoose ? ` · ${toChoose} row(s) still need a category or account` : ''}
            </div>
          </div>
          {progress ? (
            <div style={{ minWidth: 220 }}>
              <div className="progress"><i style={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%`, background: 'var(--primary)' }} /></div>
              <div className="muted small">{progress.label} {progress.done} of {progress.total}</div>
            </div>
          ) : (
            <button className="btn" disabled={checking || !info.can_import} onClick={runImport}
                    title={info.blocked ? 'Fix the problems in the file and upload it again' : toChoose ? 'Choose the categories and accounts first' : ''}>
              {info.replaces ? 'Import and replace' : 'Import'} all {records.filter((r) => r.status !== 'IGNORED').length} record(s)
            </button>
          )}
        </div>
        <p className="hint">
          Records keep their original date and appear in Expenses, Balance, the Dashboard and Reports; transfers move money between your accounts and opening balances are added to them. They can be edited for 3 days from today, don’t trigger budget alerts, and post to your books if you use Accounting. To correct the import later, use Purge &amp; re-import in Import history with the corrected file.
        </p>
      </div>
    </>
  )
}

function Row({ r, hasSplit, readOnly, onEdit, onRemove }) {
  const [cls, label] = STATUS[r.status] || ['info', r.status]
  const notes = [...(r.problems || []), ...(r.warnings || [])]
  const num = (v) => (v === '' ? null : v)
  if (readOnly) return <FileRow r={r} hasSplit={hasSplit} cls={cls} label={label} notes={notes} onEdit={onEdit} />
  return (
    <tr className={r.can_import ? '' : 'row-problem'}>
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
          <option value="TRANSFER">Transfer</option>
          <option value="OPENING">Opening balance</option>
        </select>
        {r.effective_kind === 'TRANSFER' && r.kind !== 'TRANSFER' && <div className="hint" style={{ margin: '4px 0 0' }}>→ moved, not spent</div>}
      </td>
      <td>
        <input value={r.description || ''} maxLength={300} onChange={(e) => onEdit({ description: e.target.value })} />
        {r.alias_name && <div className="hint" style={{ margin: '4px 0 0' }}>→ shown as <b>{r.alias_name}</b></div>}
        <input value={r.note || ''} maxLength={500} placeholder="Note (optional)" aria-label={`Note for row ${r.row_no}`}
               style={{ marginTop: 4 }} onChange={(e) => onEdit({ note: e.target.value || null })} />
      </td>
      <td>
        <input value={r.category || ''} maxLength={100} placeholder={r.kind === 'INCOME' ? 'Income' : ''} onChange={(e) => onEdit({ category: e.target.value || null })} />
        {r.category_final && r.category_final !== r.category && <div className="hint" style={{ margin: '4px 0 0' }}>→ {r.category_final}</div>}
      </td>
      <td className="num">
        <input type="number" min={r.kind === 'OPENING' ? undefined : '0'} step="0.01" value={r.amount ?? ''} style={{ width: 120 }} onChange={(e) => onEdit({ amount: num(e.target.value) })} />
      </td>
      {hasSplit && (
        <td className="num">
          {r.kind === 'SPLIT' && (
            <>
              <input type="number" min="0" step="0.01" value={r.my_share ?? ''} style={{ width: 110 }} onChange={(e) => onEdit({ my_share: num(e.target.value) })} />
              <input value={r.paid_by || ''} maxLength={100} placeholder="Paid by: you" title="Who paid the bill (blank = you)" style={{ width: 110, marginTop: 4 }} onChange={(e) => onEdit({ paid_by: e.target.value || null })} />
              <div className="hint" style={{ margin: '4px 0 0' }}>{r.paid_by ? `you owe ${r.paid_by} your share` : `with ${(r.split_with || []).join(', ') || 'Others'}`}</div>
            </>
          )}
        </td>
      )}
      <td style={{ minWidth: 170 }}>
        <input value={r.account || ''} maxLength={100} placeholder={r.kind === 'TRANSFER' ? 'From' : ''} onChange={(e) => onEdit({ account: e.target.value || null })} />
        {r.kind === 'TRANSFER' && (
          <input value={r.to_account || ''} maxLength={100} placeholder="To" style={{ marginTop: 4 }} onChange={(e) => onEdit({ to_account: e.target.value || null })} />
        )}
        {r.account_label && (
          <div className="hint" style={{ margin: '4px 0 0' }}>
            {r.account_label}
            {r.to_account_label && <> <ArrowRight size={11} /> {r.to_account_label}</>}
          </div>
        )}
      </td>
      <td>
        <button className="btn ghost small" title="Remove this row from the import" onClick={onRemove}>
          <Trash2 size={14} />
        </button>
      </td>
    </tr>
  )
}

/** A row from a file, as the file has it: it is changed by fixing the file. Only a note of one's own can be added. */
function FileRow({ r, hasSplit, cls, label, notes, onEdit }) {
  return (
    <tr className={r.can_import || r.status === 'IGNORED' ? '' : 'row-problem'}>
      <td className="muted">{r.row_no}</td>
      <td style={{ minWidth: 150 }}>
        <span className={`badge ${cls}`}>{label}</span>
        {notes.map((n, k) => (
          <div key={k} className="hint" style={{ margin: '4px 0 0' }}>{n}</div>
        ))}
      </td>
      <td>{r.date ? new Date(r.date).toLocaleDateString('en-IN') : <span className="muted">—</span>}{r.time && <div className="muted small">{r.time}</div>}</td>
      <td>
        {KIND_LABEL[r.kind] || <span className="muted">—</span>}
        {r.effective_kind === 'TRANSFER' && r.kind !== 'TRANSFER' && <div className="hint" style={{ margin: '4px 0 0' }}>→ moved, not spent</div>}
      </td>
      <td>
        {r.description || <span className="muted">—</span>}
        {r.alias_name && <div className="hint" style={{ margin: '4px 0 0' }}>→ shown as <b>{r.alias_name}</b></div>}
        {r.status !== 'IGNORED' && (
          <input value={r.note || ''} maxLength={500} placeholder="Note (optional)" aria-label={`Note for row ${r.row_no}`}
                 style={{ marginTop: 4 }} onChange={(e) => onEdit({ note: e.target.value || null })} />
        )}
      </td>
      <td>
        {r.category || <span className="muted">—</span>}
        {r.category_final && r.category_final !== r.category && <div className="hint" style={{ margin: '4px 0 0' }}>→ {r.category_final}</div>}
      </td>
      <td className="num">{r.amount != null && r.amount !== '' ? money(r.amount) : <span className="muted">—</span>}</td>
      {hasSplit && <td className="num">{r.kind === 'SPLIT' && r.my_share != null ? money(r.my_share) : ''}</td>}
      <td style={{ minWidth: 150 }}>
        {r.account || <span className="muted">—</span>}
        {r.to_account && <> <ArrowRight size={11} /> {r.to_account}</>}
        {r.account_label && (
          <div className="hint" style={{ margin: '4px 0 0' }}>
            {r.account_label}
            {r.to_account_label && <> <ArrowRight size={11} /> {r.to_account_label}</>}
          </div>
        )}
      </td>
      <td></td>
    </tr>
  )
}

/** Payees in the file, grouped by UPI ID / name: their alias, or a name and category to give them now. */
function PayeeMap({ info, drafts, setDrafts, pending, onSave, busy, renameOld, setRenameOld }) {
  const [side, setSide] = useState('all')
  const [showAll, setShowAll] = useState(false)
  const payees = info.payees || []
  if (!payees.length) return null
  const named = payees.filter((p) => p.alias_id).length
  const list = payees.filter((p) => side === 'all' || (side === 'sent' ? p.sent_count : p.received_count))
  const shown = showAll ? list : list.slice(0, 12)
  const set = (key, patch) => setDrafts((d) => ({ ...d, [key]: { ...d[key], ...patch } }))
  const useSuggestions = () =>
    setDrafts((d) => {
      const next = { ...d }
      for (const p of list) if (!p.alias_id && !next[p.payee_key]?.alias) next[p.payee_key] = { ...next[p.payee_key], alias: p.suggestion }
      return next
    })

  return (
    <div className="map-block">
      <div className="card-head wrap" style={{ marginBottom: 8 }}>
        <div>
          <h4 style={{ margin: 0 }}>
            <Tag size={15} style={{ verticalAlign: '-2px' }} /> Payees in this file
          </h4>
          <p className="hint" style={{ margin: 0 }}>
            {named} of {payees.length} already have an alias: their rows get its name and category, so they need no category mapping. Give the others a
            name (and a category) and every record from or to them, now and later, is shown that way.
          </p>
        </div>
        <div className="segmented" role="tablist" aria-label="Sent or received">
          {[
            ['all', `All (${payees.length})`],
            ['sent', `Sent (${payees.filter((p) => p.sent_count).length})`],
            ['received', `Received (${payees.filter((p) => p.received_count).length})`],
          ].map(([k, l]) => (
            <button key={k} className={side === k ? 'on' : ''} onClick={() => setSide(k)}>
              {l}
            </button>
          ))}
        </div>
      </div>
      <datalist id="payee-categories">
        {info.categories.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <div className="table-wrap">
        <table className="table mini-table">
          <thead>
            <tr>
              <th>Payee</th>
              <th className="num">Sent</th>
              <th className="num">Received</th>
              <th>Show as</th>
              <th>Category</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => {
              const d = drafts[p.payee_key] || {}
              const changed = pending.includes(p)
              return (
                <tr key={p.payee_key}>
                  <td style={{ wordBreak: 'break-word', maxWidth: 320 }}>
                    <b>{p.description}</b>
                    <div className="hint">
                      <code>{p.payee_key}</code> {p.alias_id && !changed && <span className="badge ok">alias</span>}
                      {changed && <span className="badge warn">not saved</span>}
                    </div>
                  </td>
                  <td className="num">{p.sent_count ? <>{money(p.sent_total)}<div className="hint">{p.sent_count}×</div></> : '—'}</td>
                  <td className="num">{p.received_count ? <><span className="net-pos">{money(p.received_total)}</span><div className="hint">{p.received_count}×</div></> : '—'}</td>
                  <td>
                    <input value={d.alias ?? p.alias ?? ''} placeholder={p.suggestion} maxLength={100} style={{ minWidth: 150 }}
                           onChange={(e) => set(p.payee_key, { alias: e.target.value })} aria-label={`Alias for ${p.description}`} />
                  </td>
                  <td>
                    <input value={d.category ?? p.alias_category ?? ''} list="payee-categories" placeholder={p.sent_count ? 'Pick or type' : '—'} maxLength={50}
                           style={{ minWidth: 130 }} disabled={!p.sent_count} title={p.sent_count ? '' : 'Money received has no category'}
                           onChange={(e) => set(p.payee_key, { category: e.target.value })} aria-label={`Category for ${p.description}`} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="form-actions" style={{ justifyContent: 'space-between', flexWrap: 'wrap', marginTop: 8 }}>
        <div className="actions">
          {list.length > 12 && (
            <button className="btn ghost small" onClick={() => setShowAll(!showAll)}>
              {showAll ? 'Show fewer' : `Show all ${list.length}`}
            </button>
          )}
          <button className="btn ghost small" onClick={useSuggestions} title="Fill the empty names with the suggested ones">
            <Sparkles size={14} /> Use suggested names
          </button>
          <label className="check small">
            <input type="checkbox" checked={renameOld} onChange={(e) => setRenameOld(e.target.checked)} /> Also rename matching records already in the app
          </label>
        </div>
        <button className="btn secondary" disabled={busy || !pending.length} onClick={onSave}>
          Save {pending.length || ''} name{pending.length === 1 ? '' : 's'} &amp; apply
        </button>
      </div>
    </div>
  )
}

function CategoryMap({ info, options, setOption }) {
  const treated = (c) => !!options.category_treatment[c.value.toLowerCase()]
  const unknown = info.category_values.filter((c) => c.expense_rows && !c.known && !treated(c))
  if (!unknown.length) return null
  const open = unknown.filter((c) => !options.category_map[c.value.toLowerCase()])
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
  const bulk = (useSuggestions) => {
    const map = { ...options.category_map }
    for (const c of open) {
      if (useSuggestions && c.suggested) map[c.value.toLowerCase()] = c.suggested
      else if (!useSuggestions && c.value.trim()) map[c.value.toLowerCase()] = c.value.trim()
    }
    setOption({ category_map: map })
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div className="toolbar wrap" style={{ padding: 0, border: 0 }}>
        <h4 style={{ margin: 0 }}>Categories not in the app yet ({open.length} to choose of {unknown.length})</h4>
        <span className="spacer" />
        {open.some((c) => c.suggested) && (
          <button className="btn secondary small" onClick={() => bulk(true)}><Sparkles size={14} /> Use the suggestions</button>
        )}
        {open.length > 0 && <button className="btn secondary small" onClick={() => bulk(false)}>Create the rest as new categories</button>}
      </div>
      <p className="hint" style={{ marginTop: 4 }}>
        Choose an existing category for each, or create it. Rows stay unimported until their category is chosen.
        {unknown.some((c) => c.maybe) && ' A “maybe” is not applied by “Use the suggestions”: check what its rows hold first (a “Mobile” category can hold recharges and a new phone).'}
      </p>
      <div className="form-grid">
        {unknown.map((c) => {
          const mapped = options.category_map[c.value.toLowerCase()]
          const isNew = mapped && !info.categories.includes(mapped)
          return (
            <div key={c.value}>
              <label>
                {c.value ? `“${c.value}”` : 'Rows without a category'} · {c.expense_rows} row(s)
                {c.suggested && !mapped ? ` · suggested: ${c.suggested}` : ''}
                {c.maybe && !mapped ? ` · maybe ${c.maybe}? Please check` : ''}
              </label>
              <select value={mapped ? (isNew ? `new:${mapped}` : mapped) : ''} onChange={(e) => choose(c.value, e.target.value.startsWith('new:') ? e.target.value.slice(4) : e.target.value)}>
                <option value="">Choose…</option>
                {c.value && <option value={`new:${c.value.trim()}`}>Create “{c.value.trim()}” as a new category</option>}
                {isNew && mapped !== c.value.trim() && <option value={`new:${mapped}`}>New category “{mapped}”</option>}
                <option value="__new__">Create a new category with another name…</option>
                <optgroup label="Your categories">
                  {info.categories.map((k) => (
                    <option key={k} value={k}>{k}{k === c.suggested ? ' (suggested)' : k === c.maybe ? ' (maybe)' : ''}</option>
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
  const unmatched = info.account_values.filter((a) => !a.ref)
  const key = (a) => a.value.toLowerCase()
  const valueOf = (a) => {
    if (key(a) in options.create_accounts) return `create:${options.create_accounts[key(a)]}`
    if (key(a) in options.account_map) return options.account_map[key(a)] === null ? 'none' : `id:${options.account_map[key(a)]}`
    return ''
  }
  const choose = (a, choice) => {
    const map = { ...options.account_map }
    const create = { ...options.create_accounts }
    delete map[key(a)]
    delete create[key(a)]
    if (choice === 'none') map[key(a)] = null
    else if (choice.startsWith('id:')) map[key(a)] = Number(choice.slice(3))
    else if (choice.startsWith('create:')) create[key(a)] = choice.slice(7)
    setOption({ account_map: map, create_accounts: create })
  }
  const createAll = () => {
    const create = { ...options.create_accounts }
    for (const a of unmatched) if (!(key(a) in options.account_map)) create[key(a)] = a.suggested_kind
    setOption({ create_accounts: create })
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div className="toolbar wrap" style={{ padding: 0, border: 0 }}>
        <h4 style={{ margin: 0 }}>Accounts named in the file</h4>
        <span className="spacer" />
        {unmatched.length > 0 && <button className="btn secondary small" onClick={createAll}><Plus size={14} /> Create the {unmatched.length} missing account(s)</button>}
      </div>
      <p className="hint" style={{ marginTop: 4 }}>
        Matched to your accounts by name (dots, spaces and capitals ignored) or last 4 digits. Choose or create the others; transfers and opening balances need both their accounts.
      </p>
      <div className="form-grid">
        {info.account_values.map((a) => {
          const current = a.matched_name
          return (
            <div key={a.value}>
              <label>{a.value ? `“${a.value}”` : 'Rows without an account'} · {a.rows} row(s)</label>
              <select value={valueOf(a)} onChange={(e) => choose(a, e.target.value)}>
                <option value="">
                  {current && !a.mapped
                    ? `Matched: ${current}${a.matched_inactive ? ' (inactive: it will be used again)' : ''}`
                    : a.ref?.startsWith('new:') ? `New account: ${a.new_name}` : 'Choose…'}
                </option>
                <optgroup label={`Create “${a.new_name || a.value}” as`}>
                  {Object.entries(ACCOUNT_KINDS).map(([k, label]) => (
                    <option key={k} value={`create:${k}`}>{label}{k === a.suggested_kind ? ' (suggested)' : ''}</option>
                  ))}
                </optgroup>
                <optgroup label="Your accounts">
                  {info.accounts.map((x) => (
                    <option key={x.id} value={`id:${x.id}`}>{x.name}{x.last4 ? ` ••${x.last4}` : ''}</option>
                  ))}
                </optgroup>
                <option value="none">No account (unassigned)</option>
              </select>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TreatmentMap({ info, options, setOption }) {
  const [showAll, setShowAll] = useState(false)
  const all = info.category_values.filter((c) => c.value.trim())
  if (!all.length) return null
  const key = (c) => c.value.toLowerCase()
  // "new:INVESTMENT:Investments" once that account exists is shown as the account itself
  const resolve = (v) => {
    const a = v && info.accounts.find((x) => v === `new:${x.kind}:${x.name}`)
    return a ? `id:${a.id}` : v
  }
  const chosen = (c) => resolve(options.category_treatment[key(c)] || '')
  const list = showAll ? all : all.filter((c) => c.suggested_treatment || chosen(c))
  const set = (c, value) => {
    const t = { ...options.category_treatment }
    if (value) t[key(c)] = value
    else delete t[key(c)]
    setOption({ category_treatment: t })
  }
  const useSuggestions = () => {
    const t = { ...options.category_treatment }
    for (const c of all) if (c.suggested_treatment && !(key(c) in t)) t[key(c)] = resolve(c.suggested_treatment)
    setOption({ category_treatment: t })
  }
  const pending = all.filter((c) => c.suggested_treatment && !(key(c) in options.category_treatment)).length
  return (
    <div style={{ marginTop: 16 }}>
      <div className="toolbar wrap" style={{ padding: 0, border: 0 }}>
        <h4 style={{ margin: 0 }}>Money that isn’t spending or income</h4>
        <span className="spacer" />
        {pending > 0 && <button className="btn secondary small" onClick={useSuggestions}><Sparkles size={14} /> Use the {pending} suggestion(s)</button>}
        <button className="btn ghost small" onClick={() => setShowAll(!showAll)}>{showAll ? 'Only suggested' : 'All categories'}</button>
      </div>
      <p className="hint" style={{ marginTop: 4 }}>
        Investments, money paid for someone and paid back, returns to family… Moving them to an account keeps your bank balances right without counting them as spending or income.
      </p>
      {list.length ? (
        <div className="form-grid">
          {list.map((c) => (
            <div key={c.value}>
              <label>“{c.value}” · {c.rows} row(s){c.suggested_treatment && !chosen(c) ? ' · suggested: move it' : ''}</label>
              <select value={chosen(c)} onChange={(e) => set(c, e.target.value)}>
                <option value="">Spending / income (as in the file)</option>
                {TREAT_NEW.filter(([v]) => !info.accounts.some((a) => `new:${a.kind}:${a.name}` === v)).map(([v, l]) => (
                  <option key={v} value={v}>Move to {l}</option>
                ))}
                {info.accounts.map((a) => (
                  <option key={a.id} value={`id:${a.id}`}>Move to {a.name}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">No category looks like it. Use “All categories” to move one anyway.</p>
      )}
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
        {result.accounts_created?.length ? ` New account(s): ${result.accounts_created.join(', ')}.` : ''}
      </Alert>
      <p>
        Spending {money(result.expense_total)} · Money received {money(result.income_total)}
      </p>
      {result.replaced && !result.replaced.error && (
        <Alert type="ok">Replaced “{result.replaced.filename}”: its {result.replaced.removed} record(s) were removed.</Alert>
      )}
      {result.replaced?.error && (
        <Alert type="error">
          The new file is in, but removing “{result.replaced.filename}” stopped ({result.replaced.error}). Open Import history and press
          “Finish replacing” on it, so nothing stays twice.
        </Alert>
      )}
      <BelowZero warnings={result.account_warnings} />
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
    row_no: r.row_no, kind: r.kind || null, date: r.date || null, time: r.time || null, description: r.description || '', category: r.category || null,
    amount: r.amount === '' ? null : r.amount, my_share: r.kind === 'SPLIT' ? (r.my_share === '' ? null : r.my_share) : null,
    split_with: r.split_with || [], paid_by: r.kind === 'SPLIT' ? r.paid_by || null : null, account: r.account || null, to_account: r.kind === 'TRANSFER' ? r.to_account || null : null,
    note: r.note || null, quantity: r.quantity || 1,
    include: true, parse_errors: r.parse_errors || [], parse_warnings: r.parse_warnings || [], fingerprint: r.fingerprint || null,
  }
}
