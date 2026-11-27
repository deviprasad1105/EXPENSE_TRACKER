import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, ArrowRight, Check, CircleCheckBig, Columns3, FileCheck2, FileUp, History, Keyboard, ListChecks, Lock, Plus, RefreshCw,
  SkipForward, Sparkles, Trash2, UploadCloud, UsersRound, WandSparkles,
} from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { ACCOUNT_KINDS } from '../components/Accounts'
import { Alert, EmptyRow, Msg, PageHeader } from '../components/ui'
import DateInput from '../components/DateInput'
import PasswordInput from '../components/PasswordInput'

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
  ['balance', 'Balance (checks the statement; not imported)'],
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
  IMPORTED_BEFORE: ['info', 'Already imported · skipped'],
  REPEATED: ['warn', 'Repeated in the file'],
  IGNORED: ['info', 'Not a transaction'],
}
const FILTERS = [
  ['all', 'All', () => true],
  ['ready', 'Ready', (r) => r.status === 'READY'],
  ['attention', 'Needs attention', (r) => !r.can_import && r.status !== 'IMPORTED_BEFORE' && r.status !== 'IGNORED'],
  ['imported', 'Already imported', (r) => r.status === 'IMPORTED_BEFORE'],
  ['duplicates', 'Possible duplicates', (r) => r.status === 'DUPLICATE' || r.status === 'REPEATED'],
  ['transfers', 'Transfers', (r) => r.effective_kind === 'TRANSFER' || r.kind === 'OPENING'],
  ['out', 'Left out', (r) => r.can_import && !r.include],
  ['statement', 'Statement mismatch', (r) => (r.warnings || []).some((w) => w.includes('misread or is missing') || w.includes('closing balance') || w.includes('No amount or type'))],
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

const KIND_NAME = { WALLET: 'wallet', CASH: 'cash account', SAVINGS: 'bank account', CURRENT: 'bank account', SALARY: 'bank account' }

// Accounts (bank, cash, wallet) that would end below zero: some of their rows are probably missing from the file
function BelowZero({ warnings }) {
  if (!warnings?.length) return null
  return (
    <Alert type="warn">
      {warnings.map((w) =>
        w.kind === 'RECEIVABLE' || w.kind === 'INVESTMENT' ? (
          <div key={w.account}>
            “{w.account}” would end at {money(w.balance)}:{' '}
            {w.kind === 'RECEIVABLE'
              ? 'more would come back to you than you gave or lent. The money you gave is probably missing from this file, or some of the money received moved there is really income.'
              : 'more would be taken out than was put in. Some of what is moved out is probably returns or gains (income), or the money you invested is missing from this file.'}{' '}
            Check the moves under “Money that isn’t spending or income”.
          </div>
        ) : (
        <div key={w.account}>
          “{w.account}” would end at {money(w.balance)}. A {KIND_NAME[w.kind] || 'account'} can’t go below zero, so some of its
          {w.kind === 'WALLET' ? ' top-ups' : ' money in'} are probably missing from this file. Import that {KIND_NAME[w.kind] || 'account'}’s own
          statement (PDF, Excel or CSV) as well to complete it.
        </div>
        ),
      )}
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
      <button className={active === 'purge' ? 'on' : ''} onClick={() => navigate('/import/purge')}>
        <Trash2 size={14} /> Purge
      </button>
      <button className={active === 'history' ? 'on' : ''} onClick={() => navigate('/import/history')}>
        <History size={14} /> Import history
      </button>
    </div>
  )
}

// The import, one step at a time
const STEPS = [
  { title: 'Upload', hint: 'Choose the file', Icon: UploadCloud },
  { title: 'Columns', hint: 'What each column holds', Icon: Columns3 },
  { title: 'Payees', hint: 'Name them, or skip', Icon: UsersRound },
  { title: 'Needs action', hint: 'Fix, auto-analyse or skip', Icon: WandSparkles },
  { title: 'Review & import', hint: 'Check and import', Icon: FileCheck2 },
]

function Stepper({ current, done }) {
  return (
    <ol className="stepper" aria-label="Import steps">
      {STEPS.map(({ title, hint, Icon }, i) => {
        const state = done || i < current ? 'done' : i === current ? 'current' : 'todo'
        return (
          <li key={title} className={`step ${state}`} aria-current={state === 'current' ? 'step' : undefined}>
            <span className="step-icon">{state === 'done' ? <Check size={26} strokeWidth={2.6} /> : <Icon size={26} />}</span>
            <span className="step-text">
              <small>Step {i + 1}</small>
              <b>{title}</b>
              <span>{hint}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/** Back / Next at the foot of each step (stays in view while the step scrolls). */
function StepNav({ onBack, backLabel = 'Back', onNext, nextLabel = 'Next', nextDisabled, note, children }) {
  return (
    <div className="step-nav">
      {onBack ? (
        <button className="btn secondary" onClick={onBack}>
          <ArrowLeft size={16} /> {backLabel}
        </button>
      ) : <span />}
      <span className="step-note muted small">{note}</span>
      {children || (onNext && (
        <button className="btn" onClick={onNext} disabled={nextDisabled}>
          {nextLabel} <ArrowRight size={16} />
        </button>
      ))}
    </div>
  )
}

export default function ImportRecords() {
  const { target, user } = useAuth()
  const [msg, setMsg] = useState(null)
  const [file, setFile] = useState(null) // what /extract returned
  const [step, setStep] = useState(0) // 0 upload, 1 columns; 2-4 are inside Review
  const [review, setReview] = useState(null) // { filename, file_type, file_hash, rows, mapping }
  const [reviewStep, setReviewStep] = useState(2)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [locked, setLocked] = useState(null) // { f, error }: a password-protected file waiting for its password
  const viewingOther = target && target.id !== user.id
  // Purge & re-import: the earlier import the next file replaces (from Import history, or offered after an upload)
  const [params, setParams] = useSearchParams()
  const replaceId = Number(params.get('replace')) || null
  const setReplace = (id) => setParams(id ? { replace: String(id) } : {})

  // Read a file; a locked one (423) asks for its password and is sent again with it. The password is only sent
  // with this upload: it is never saved.
  const read = async (f, password) => {
    setMsg(null)
    setBusy(true)
    const form = new FormData()
    try {
      const [body, name] = await uploadable(f)
      form.append('file', body, name)
      if (password) form.append('password', password)
      const res = await api.post('/imports/extract', form)
      setLocked(null)
      setFile(res.data)
    } catch (err) {
      if (err.response?.status === 423) setLocked({ f, error: password ? errorMessage(err) : null })
      else {
        setLocked(null)
        setMsg({ type: 'error', text: errorMessage(err) })
      }
    } finally {
      setBusy(false)
    }
  }

  const take = (f) => {
    if (!f) return
    setResult(null)
    setReview(null)
    setFile(null)
    setLocked(null)
    setStep(0)
    read(f)
  }
  const choose = (input) => {
    const f = input.files[0]
    input.value = '' // so choosing the same file again still triggers a read
    take(f)
  }
  const drop = (e) => {
    e.preventDefault()
    setDragging(false)
    if (!busy) take(e.dataTransfer.files?.[0])
  }

  const startManual = () => {
    setMsg(null)
    setResult(null)
    setFile(null)
    setReviewStep(4)
    setReview({ filename: 'Typed by hand', file_type: 'manual', file_hash: null, rows: null, mapping: null })
  }
  const restart = () => {
    setResult(null)
    setFile(null)
    setReview(null)
    setStep(0)
    setMsg(null)
    setReplace(null)
  }
  const current = result ? STEPS.length : review ? reviewStep : step

  return (
    <div>
      <PageHeader title="Import past records" subtitle="Bring in expenses and money received from before you used the app: from another app, bank statements or your own sheets.">
        <ImportTabs active="new" />
      </PageHeader>
      {viewingOther && <Alert type="info">Imports always go into your own account, not the data you are viewing.</Alert>}
      <Stepper current={current} done={!!result} />
      <Msg msg={msg} />
      {replaceId && !result && (
        <Alert type="info">
          Purge &amp; re-import: the file you choose replaces an earlier import. Once it is imported, that import’s records are removed
          (also those edited since).{' '}
          <button className="btn ghost small" onClick={() => setReplace(null)}>Cancel</button>
        </Alert>
      )}

      {result ? (
        <Done result={result} onAgain={restart} />
      ) : review ? (
        <Review
          key={`${replaceId || 0}`}
          source={review}
          replaceId={replaceId}
          onReplace={setReplace}
          step={reviewStep}
          setStep={setReviewStep}
          onBack={() => { setReview(null); setStep(file ? 1 : 0) }}
          onCancel={restart}
          onDone={(r) => { setReview(null); setFile(null); setResult(r) }}
          onError={(text) => setMsg({ type: 'error', text })}
        />
      ) : step === 1 && file ? (
        <Columns
          file={file}
          onBack={() => setStep(0)}
          onRead={(rows, mapping, tableIndex) => {
            setReviewStep(2)
            setReview({ ...file, rows, mapping, other_tables: summaryRows(file.tables, tableIndex) })
          }}
          onError={(text) => setMsg({ type: 'error', text })}
        />
      ) : (
        <div className="card">
          <label
            className={`dropzone ${dragging ? 'over' : ''} ${file ? 'has-file' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
          >
            <input type="file" accept={ACCEPT} hidden disabled={busy} onChange={(e) => choose(e.target)} />
            <span className="dropzone-icon">
              {busy ? <RefreshCw size={44} className="spin" /> : file ? <CircleCheckBig size={44} /> : <UploadCloud size={44} />}
            </span>
            {busy ? (
              <b>Reading the file…</b>
            ) : file ? (
              <>
                <b>{file.filename}</b>
                <span className="muted">
                  {file.tables.length} table(s) found · {file.tables[file.table_index]?.rows.length ?? 0} rows in the main one. Click to choose another file.
                </span>
              </>
            ) : (
              <>
                <b>Drop your file here, or click to choose</b>
                <span className="muted">Excel (.xlsx, .xls), CSV, PDF or Word (.docx) · up to 5 MB and about 5,000 records</span>
              </>
            )}
          </label>
          {file?.already_imported && !replaceId && (
            <Alert type="warn">
              This file was imported on {new Date(file.already_imported.date).toLocaleDateString('en-IN')} as “{file.already_imported.filename}”
              ({file.already_imported.rows} records). Rows already imported are recognised and skipped. To correct that import instead,
              replace it with this file:{' '}
              <button className="btn small" onClick={() => setReplace(file.already_imported.batch_id)}>Purge &amp; re-import</button>
            </Alert>
          )}
          {locked && (
            <PasswordPrompt
              key={locked.f.name}
              filename={locked.f.name}
              error={locked.error}
              busy={busy}
              onSubmit={(pw) => read(locked.f, pw)}
              onCancel={() => setLocked(null)}
            />
          )}
          <p className="hint" style={{ marginTop: 12 }}>
            Password-protected statements are fine: you’ll be asked for the password. Scanned PDFs and photos of receipts can’t be read yet;
            old Word .doc files: save them as .docx or PDF first.
          </p>
          <StepNav
            note={file ? 'Next: check what each column holds' : null}
            onNext={file ? () => setStep(1) : null}
            nextLabel="Next: columns"
          >
            {!file && (
              <button className="btn secondary" onClick={startManual}>
                <Keyboard size={16} /> Type records instead
              </button>
            )}
          </StepNav>
        </div>
      )}
    </div>
  )
}

// Rows of the file's other tables that may hold the statement's totals (a label row and the row below it)
const SUMMARY_WORDS = /total|balance|debit|credit|withdraw|deposit|count|brought|carried|\bdr\b|\bcr\b/i
function summaryRows(tables, current) {
  return (tables || [])
    .filter((_, i) => i !== current)
    .map((t) => t.rows.filter((r, i) => SUMMARY_WORDS.test(r.join(' ')) || (i > 0 && SUMMARY_WORDS.test(t.rows[i - 1].join(' ')))).slice(0, 200))
    .filter((rows) => rows.length)
    .slice(0, 20)
}

/** The records compared with the statement's own figures: totals, counts, opening → closing balance, running balance. */
function StatementCheck({ check, accept, setAccept, onShowRows }) {
  if (!check) return null
  if (!check.available)
    return <p className="hint">This file has no totals or balance column to check the records against: compare the totals below with your statement before importing.</p>
  const rows = check.balances_verified ? ` · running balance checked on ${check.balances_verified} row(s) (${check.balance_order})` : ''
  return (
    <div className={`statement-check ${check.ok ? 'ok' : 'bad'}`}>
      <div className="strong">
        {check.ok ? '✓ Matches the statement' : '✗ The records don’t match the statement'}
        <span className="muted small" style={{ fontWeight: 400 }}>{rows}</span>
      </div>
      {check.checks.length > 0 && (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr><th>Check</th><th className="num">Statement says</th><th className="num">Read from the file</th><th></th></tr>
            </thead>
            <tbody>
              {check.checks.map((c) => (
                <tr key={c.key}>
                  <td>{c.label}</td>
                  <td className="num">{c.statement}</td>
                  <td className="num">{c.read}</td>
                  <td>{c.ok ? <span className="badge ok">OK</span> : <span className="badge bad">Off by {c.difference}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {check.row_issue_count > 0 && (
        <>
          <ul className="statement-issues">
            {check.row_issues.slice(0, 5).map((i, k) => (
              <li key={k}>Row {i.row_no}: {i.message}</li>
            ))}
            {check.row_issue_count > 5 && <li>…and {check.row_issue_count - 5} more</li>}
          </ul>
          <button className="btn secondary small" onClick={onShowRows}>Show these rows</button>
        </>
      )}
      {!check.ok && (
        <>
          <p className="hint" style={{ margin: 0 }}>
            Usually a row was read wrongly or missed (e.g. in a PDF). Correct the amount on the row shown, add the missing record, or check the
            column choices (Start over). Import stays off until the records match, or you confirm below.
          </p>
          <label className="check">
            <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
            I’ve checked the differences and want to import anyway
          </label>
        </>
      )}
    </div>
  )
}

function PasswordPrompt({ filename, error, busy, onSubmit, onCancel }) {
  const [password, setPassword] = useState('')
  const submit = (e) => {
    e.preventDefault()
    if (password) onSubmit(password)
  }
  return (
    <form className="password-prompt" onSubmit={submit}>
      <p>
        <Lock size={14} /> <strong>{filename}</strong> is password-protected. Enter the password to open it (bank statements often use
        part of your name, date of birth or customer ID). It is only used to read the file and isn’t saved.
      </p>
      {error && <Alert type="error">{error}</Alert>}
      <div className="filters">
        <PasswordInput
          autoFocus
          autoComplete="off"
          placeholder="File password"
          value={password}
          disabled={busy}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="btn" type="submit" disabled={busy || !password}>
          {busy ? 'Opening…' : 'Open file'}
        </button>
        <button className="btn secondary" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function Columns({ file, onRead, onBack, onError }) {
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
          <h3>Check the columns</h3>
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
      <StepNav
        onBack={onBack}
        note={missing || `${rows.length - start - (mapping.header_row >= 0 ? 1 : 0)} rows under the heading`}
        onNext={() => onRead(rows, mapping, tableIndex)}
        nextDisabled={!!missing}
        nextLabel="Next: read the records"
      />
    </div>
  )
}

function Review({ source, replaceId, onReplace, step, setStep, onBack, onCancel, onDone, onError }) {
  const [records, setRecords] = useState([])
  const [info, setInfo] = useState(null) // the last check: summary, categories, accounts…
  // A file with an account column uses it (transfers and opening balances name their accounts anyway)
  const [options, setOptions] = useState({
    account_mode: source.mapping?.account != null ? 'column' : 'primary', account_id: null, account_map: {}, create_accounts: {},
    category_map: {}, category_treatment: {}, split_group_id: null,
  })
  const [checking, setChecking] = useState(false)
  const [statement, setStatement] = useState(null) // the statement's own totals / balances, as /preview read them
  const [acceptMismatch, setAcceptMismatch] = useState(false)
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(0)
  const [progress, setProgress] = useState(null)
  // Alias names / categories typed in "Payees in this file", not saved yet: { payee key: { alias, category } }
  const [drafts, setDrafts] = useState({})
  const [renameOld, setRenameOld] = useState(true)
  const [skippedPayees, setSkippedPayees] = useState(() => new Set())
  const [skippedRows, setSkippedRows] = useState(() => new Set())
  const [remembered, setRemembered] = useState(null) // what was filled in from the last import
  // Purge & re-import: other imports this file replaces too (rows of it that came in with another file)
  const [also, setAlso] = useState([])
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
          ? await api.post('/imports/preview', {
              rows: source.rows, mapping: source.mapping, options, other_tables: source.other_tables || [],
              replaces_id: replaceId, also_replaces: also,
            })
          : await api.post('/imports/check', { records: [], options })
        if (res.data.statement) setStatement(res.data.statement)
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
          const again = await api.post('/imports/check', {
            records: res.data.records.map(clean), options: next, statement: res.data.statement, replaces_id: replaceId, also_replaces: also,
          })
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

  // Purge & re-import of a period imported in several files: replace the other imports too, or not
  const addAlso = (id) => {
    const next = [...new Set([...also, id])]
    setAlso(next)
    check(records, options, next)
  }
  const dropAlso = (id) => {
    const next = also.filter((x) => x !== id)
    setAlso(next)
    check(records, options, next)
  }

  const check = async (recs, opts, alsoIds = also) => {
    const v = ++version.current
    setChecking(true)
    try {
      const res = await api.post('/imports/check', {
        records: recs.map(clean), options: opts, statement, replaces_id: replaceId, also_replaces: alsoIds,
      })
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
    if (!data) return null
    // Rows that became importable are ticked, as on screen
    return data.records.map((r, i) => (records[i] && !records[i].can_import && r.can_import ? { ...r, include: r.default_include } : r))
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
  const chosen = records.filter((r) => r.can_import && r.include)
  const problems = records.filter((r) => !r.can_import && r.status !== 'IMPORTED_BEFORE' && r.status !== 'IGNORED').length
  const importedBefore = records.filter((r) => r.status === 'IMPORTED_BEFORE').length
  const sumOf = (kinds) => chosen.filter((r) => kinds.includes(r.effective_kind)).reduce((s, r) => s + Number(r.amount || 0), 0)
  const totalOut = sumOf(['EXPENSE', 'SPLIT'])
  const totalIn = sumOf(['INCOME'])
  const totalMoved = sumOf(['TRANSFER'])
  const transfers = chosen.filter((r) => r.effective_kind === 'TRANSFER').length
  const openings = chosen.filter((r) => r.kind === 'OPENING').length
  const hasSplit = records.some((r) => r.kind === 'SPLIT')
  const hasAccountColumn = records.some((r) => r.account)
  const s = info?.summary
  const stmt = info?.statement_check
  const mismatch = !!(stmt?.available && !stmt.ok)

  const runImport = async () => {
    let recs = records
    if (pending.length) {
      if (!window.confirm(`Save the ${pending.length} payee name(s) you typed first, so these records get them?`)) return
      recs = await saveAliases()
      if (!recs) return
    }
    const picked = recs.filter((r) => r.can_import && r.include)
    const left = recs.length - picked.length
    if (mismatch && !acceptMismatch) return
    const olds = info.replaces ? [info.replaces, ...(info.also_replaces || [])] : []
    const replacing = olds.length
      ? `\n\nThen ${olds.map((o) => `“${o.filename}” is removed: ${o.removes} record(s)${o.edited ? `, including ${o.edited} edited since its import` : ''}`).join('; ')}.`
      : ''
    if (!window.confirm(`${mismatch ? 'The records DON’T match the statement’s totals / balances.\n\n' : ''}Import ${picked.length} record(s)?${left ? `\n\n${left} other row(s) will be listed in the import history as skipped or failed, with the reason.` : ''}${replacing}`)) return
    setProgress({ done: 0, total: recs.length, label: 'Importing…' })
    try {
      const batch = (await api.post('/imports', {
        filename: source.filename, file_type: source.file_type, file_hash: source.file_hash,
        records: recs.map(clean), options, replaces_id: replaceId, also_replaces: also,
      })).data
      const created = []
      for (let i = 0; i < recs.length; i += CHUNK) {
        const res = await api.post(`/imports/${batch.id}/rows`, { records: recs.slice(i, i + CHUNK).map(clean), options })
        created.push(...(res.data.accounts_created || []))
        setProgress({ done: Math.min(recs.length, i + CHUNK), total: recs.length, label: 'Importing…' })
      }
      const done = (await api.post(`/imports/${batch.id}/finish`)).data
      let replaced = null
      if (olds.length) {
        const names = olds.map((o) => `“${o.filename}”`).join(', ')
        let removed = 0
        try {
          for (const old of olds) {
            const res = await purgeAll(old.id, (n) => setProgress({ done: old.removes - n, total: old.removes, label: `Removing “${old.filename}”…` }))
            removed += res.removed
          }
          replaced = { removed, filename: names }
        } catch (err) {
          replaced = { error: errorMessage(err), filename: names }
        }
      }
      onReplace(null)
      onDone({ ...done, accounts_created: created, account_warnings: s?.account_warnings, replaced })
    } catch (err) {
      onError(`${errorMessage(err)}. What was imported before the error is in Import history, where it can be undone.`)
      setProgress(null)
    }
  }

  if (!info) return <div className="card muted"><RefreshCw size={16} className="spin" /> Reading the records…</div>

  const fileLine = `${source.filename} · ${records.length} record(s)${info.ignored_rows ? ` · ${info.ignored_rows} blank, heading or total row(s) skipped` : ''}`
  const checkingBadge = checking && <span className="muted small"><RefreshCw size={14} className="spin" /> Checking…</span>
  const needAction = records.map((r, i) => [r, i]).filter(([r]) => !r.can_import && r.status !== 'IMPORTED_BEFORE' && r.status !== 'IGNORED')
  const openActions = needAction.filter(([r]) => !skippedRows.has(r.row_no))
  const payeesLeft = (info.payees || []).filter((p) => !p.alias_id && !skippedPayees.has(p.payee_key)).length

  const applyPatches = (patches) => {
    const byRow = Object.fromEntries(patches.map((x) => [x.row_no, x.patch]))
    const next = records.map((r) => (byRow[r.row_no] ? { ...r, ...byRow[r.row_no], include: true } : r))
    setRecords(next)
    scheduleCheck(next)
  }
  const leavePayees = async () => {
    if (pending.length && window.confirm(`Add the ${pending.length} payee name(s) you typed?`)) {
      if (!(await saveAliases())) return
    }
    setStep(3)
  }

  if (step === 2)
    return (
      <div className="card">
        <div className="card-head wrap">
          <div>
            <h3>Payees in this file</h3>
            <p>{fileLine}</p>
          </div>
          <div className="filters">{checkingBadge}</div>
        </div>
        <PayeeStep
          info={info} drafts={drafts} setDrafts={setDrafts} busy={checking} renameOld={renameOld} setRenameOld={setRenameOld}
          skipped={skippedPayees} setSkipped={setSkippedPayees} onSaved={() => check(records, options)} onError={onError}
        />
        <StepNav onBack={onBack} backLabel="Columns" note={payeesLeft ? `${payeesLeft} payee(s) not named yet: they keep their bank description` : 'Every payee is named or skipped'}
                 onNext={leavePayees} nextLabel="Next: needs action" />
      </div>
    )

  if (step === 3)
    return (
      <div className="card">
        <div className="card-head wrap">
          <div>
            <h3>Rows that need action</h3>
            <p>{fileLine}</p>
          </div>
          <div className="filters">{checkingBadge}</div>
        </div>
        <ActionStep
          rows={needAction} records={records} skipped={skippedRows} setSkipped={setSkippedRows}
          onEdit={edit} onApply={applyPatches} onError={onError}
        />
        <StepNav onBack={() => setStep(2)} backLabel="Payees"
                 note={openActions.length ? `${openActions.length} row(s) still need action: they won’t be imported unless fixed` : `${chosen.length} record(s) ready to import`}
                 onNext={() => setStep(4)} nextLabel="Next: review & import" />
      </div>
    )

  return (
    <>
      <div className="card">
        <div className="card-head wrap">
          <div>
            <h3>Review and import</h3>
            <p>{fileLine}</p>
          </div>
          <div className="filters">{checkingBadge}</div>
        </div>

        <Alert type={chosen.length ? 'ok' : 'warn'}>
          {chosen.length ? (
            <>
              <strong>{chosen.length} of {records.length} record(s) are ready to import</strong>: categories and accounts have been
              filled in for you. You can press <b>Import</b> now, or change anything below first.
              {problems ? ` ${problems} row(s) with a problem (e.g. no amount or date) will be skipped.` : ''}
              {s?.duplicates ? ` ${s.duplicates} possible duplicate(s) are left out unless you tick them.` : ''}
            </>
          ) : importedBefore && importedBefore === records.length ? (
            <>Every record in this file was imported before: there is nothing new to import.</>
          ) : (
            <>No row can be imported yet: check that the date and amount columns were chosen correctly (Start over).</>
          )}
        </Alert>
        {importedBefore > 0 && (
          <Alert type="info">
            <strong>{importedBefore} record(s) were imported before</strong> (same UPI reference, or the same UPI ID, date and amount, or the
            same row): they are skipped automatically and listed in Import history as skipped. Everything else imports as usual.{' '}
            <button className="link-btn" onClick={() => { setFilter('imported'); setPage(0) }}>Show them</button>
          </Alert>
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
            {(info.also_replaces || []).map((o) => (
              <div key={o.id}>
                It also replaces “{o.filename}”, imported on {new Date(o.created_at).toLocaleDateString('en-IN')}: its {o.removes} record(s) are removed too.{' '}
                <button className="btn ghost small" onClick={() => dropAlso(o.id)}>Don’t replace it</button>
              </div>
            ))}
          </Alert>
        )}
        {(info.earlier_imports || []).length > 0 && (
          <Alert type="info">
            {replaceId
              ? 'Some rows of this file also came in with other imports (they are skipped). To correct those too, replace them as well:'
              : 'To correct an earlier import with this file instead of skipping the rows it already brought in, purge and re-import it:'}
            <div className="filters" style={{ marginTop: 6 }}>
              {!replaceId && info.earlier_imports.map((b) => (
                <button key={b.id} className="btn small" disabled={!b.can_replace} onClick={() => onReplace(b.id)}>
                  Purge &amp; re-import “{b.filename}” ({b.rows} row(s))
                </button>
              ))}
              {replaceId && info.earlier_imports.filter((b) => b.can_replace).map((b) => (
                <button key={b.id} className="btn small" onClick={() => addAlso(b.id)}>
                  Replace “{b.filename}” too ({b.rows} row(s) of this file came in with it)
                </button>
              ))}
            </div>
          </Alert>
        )}

        <StatementCheck check={stmt} accept={acceptMismatch} setAccept={setAcceptMismatch} onShowRows={() => { setFilter('statement'); setPage(0) }} />

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

        <AccountMap info={info} options={options} setOption={setOption} />
        <CategoryMap info={info} options={options} setOption={setOption} />
        <TreatmentMap info={info} options={options} setOption={setOption} />
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
                <th>Account</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(page * PAGE, page * PAGE + PAGE).map(([r, i]) => (
                <Row key={`${r.row_no}-${i}`} r={r} hasSplit={hasSplit}
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
        <BelowZero warnings={s?.account_warnings} />
        <div className="form-actions">
          {source.rows && (
            <button className="btn secondary" onClick={() => setStep(3)}>
              <ArrowLeft size={16} /> Needs action
            </button>
          )}
          <div className="grow-space">
            <div className="strong">{chosen.length} record(s) to import</div>
            <div className="muted small">
              Spending {money(totalOut)} · Money received {money(totalIn)}
              {transfers ? ` · ${transfers} transfer(s) of ${money(totalMoved)}` : ''}
              {openings ? ` · ${openings} opening balance(s)` : ''}
              {s?.duplicates ? ` · ${s.duplicates} possible duplicate(s) left out unless ticked` : ''}
              {s?.repeated ? ` · ${s.repeated} row(s) repeated in the file` : ''}
              {s?.ignored ? ` · ${s.ignored} balance / total line(s) left out` : ''}
              {s?.to_check ? ` · ${s.to_check} unusually large row(s) to check` : ''}
              {importedBefore ? ` · ${importedBefore} imported before: skipped` : ''}
              {problems ? ` · ${problems} row(s) with problems won’t be imported` : ''}
            </div>
          </div>
          {progress ? (
            <div style={{ minWidth: 220 }}>
              <div className="progress"><i style={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%`, background: 'var(--primary)' }} /></div>
              <div className="muted small">{progress.label || 'Importing…'} {progress.done} of {progress.total}</div>
            </div>
          ) : (
            <button className="btn" disabled={checking || !chosen.length || (mismatch && !acceptMismatch)} onClick={runImport}
                    title={mismatch && !acceptMismatch ? 'The records don’t match the statement: see “Statement check” above' : ''}>
              {info.replaces ? 'Import and replace' : 'Import'} {chosen.length} record(s)
            </button>
          )}
        </div>
        <p className="hint">
          Records keep their original date and appear in Expenses, Balance, the Dashboard and Reports; transfers move money between your accounts and opening balances are added to them. They can be edited like any other entry, don’t trigger budget alerts, and post to your books if you use Accounting. You can undo the whole import from Import history.
        </p>
      </div>
    </>
  )
}

/** Step 3: every payee, to name (alias + category) or skip, one by one or all at once. */
function PayeeStep({ info, drafts, setDrafts, busy, renameOld, setRenameOld, skipped, setSkipped, onSaved, onError }) {
  const [show, setShow] = useState('todo')
  const [limit, setLimit] = useState(20)
  const [saving, setSaving] = useState(null)
  const payees = info.payees || []
  if (!payees.length)
    return (
      <div className="empty-step">
        <UsersRound size={48} />
        <b>No payees to name in this file</b>
        <span className="muted">Press Next to continue.</span>
      </div>
    )
  const stateOf = (p) => (p.alias_id ? 'added' : skipped.has(p.payee_key) ? 'skipped' : 'todo')
  const counts = { todo: 0, added: 0, skipped: 0 }
  payees.forEach((p) => counts[stateOf(p)]++)
  const list = payees.filter((p) => show === 'all' || stateOf(p) === show)
  const set = (key, patch) => setDrafts((d) => ({ ...d, [key]: { ...d[key], ...patch } }))
  const skip = (keys, on = true) =>
    setSkipped((prev) => {
      const next = new Set(prev)
      keys.forEach((k) => (on ? next.add(k) : next.delete(k)))
      return next
    })

  const save = async (ps) => {
    setSaving(ps.length === 1 ? ps[0].payee_key : 'all')
    let done = 0
    try {
      for (const p of ps) {
        const d = drafts[p.payee_key] || {}
        const name = (d.alias ?? p.alias ?? p.suggestion ?? '').trim()
        const category = (d.category ?? p.alias_category ?? '').trim() || null
        if (!name) continue
        if (p.alias_id) await api.put(`/aliases/${p.alias_id}`, { alias: name, category, clear_category: !category })
        else await api.post('/aliases', { match_key: p.payee_key, alias: name, category, apply_all_existing: renameOld, set_category_existing: renameOld && !!category })
        done++
      }
      setDrafts((d) => {
        const next = { ...d }
        ps.forEach((p) => delete next[p.payee_key])
        return next
      })
      skip(ps.map((p) => p.payee_key), false)
    } catch (err) {
      onError(`${errorMessage(err)} (${done} of ${ps.length} payee name(s) added)`)
    }
    await onSaved()
    setSaving(null)
  }
  const todo = payees.filter((p) => stateOf(p) === 'todo')

  return (
    <>
      <p className="hint" style={{ marginTop: 0 }}>
        Give a payee a short name (an alias) and a category: all its records, now and in later imports, are shown that way. Skip the ones you don’t
        want to name; they keep the bank’s description and get a category automatically.
      </p>
      <div className="toolbar wrap" style={{ padding: '0 0 12px', border: 0 }}>
        <div className="segmented">
          {[['todo', 'To review'], ['added', 'Added'], ['skipped', 'Skipped'], ['all', 'All']].map(([k, l]) => (
            <button key={k} className={show === k ? 'on' : ''} onClick={() => { setShow(k); setLimit(20) }}>
              {l} <span className="pill">{k === 'all' ? payees.length : counts[k]}</span>
            </button>
          ))}
        </div>
        <span className="spacer" />
        {todo.length > 0 && (
          <>
            <button className="btn secondary small" disabled={!!saving || busy} onClick={() => save(todo)}>
              <Sparkles size={14} /> Add all {todo.length} with the suggested names
            </button>
            <button className="btn ghost small" onClick={() => skip(todo.map((p) => p.payee_key))}>
              <SkipForward size={14} /> Skip the rest
            </button>
          </>
        )}
      </div>
      <datalist id="payee-categories">
        {info.categories.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <div className="payee-list">
        {list.slice(0, limit).map((p) => {
          const st = stateOf(p)
          const d = drafts[p.payee_key] || {}
          return (
            <div key={p.payee_key} className={`payee-card ${st}`}>
              <span className="payee-avatar">{(p.alias || p.suggestion || p.payee_key || '?').slice(0, 1).toUpperCase()}</span>
              <div className="payee-main">
                <b title={p.description}>{p.description}</b>
                <span className="muted small">
                  {p.sent_count ? `Sent ${money(p.sent_total)} (${p.sent_count}×)` : ''}
                  {p.sent_count && p.received_count ? ' · ' : ''}
                  {p.received_count ? `Received ${money(p.received_total)} (${p.received_count}×)` : ''}
                </span>
              </div>
              <div className="payee-fields">
                <input value={d.alias ?? p.alias ?? ''} placeholder={p.suggestion || 'Name'} maxLength={100}
                       onChange={(e) => set(p.payee_key, { alias: e.target.value })} aria-label={`Name for ${p.description}`} />
                <input value={d.category ?? p.alias_category ?? ''} list="payee-categories" placeholder={p.sent_count ? 'Category' : '—'} maxLength={50}
                       disabled={!p.sent_count} onChange={(e) => set(p.payee_key, { category: e.target.value })} aria-label={`Category for ${p.description}`} />
              </div>
              <div className="payee-actions">
                {st === 'added' && !drafts[p.payee_key] ? (
                  <span className="badge ok"><Check size={12} /> Added</span>
                ) : (
                  <button className="btn small" disabled={!!saving || busy} onClick={() => save([p])}>
                    {saving === p.payee_key ? <RefreshCw size={14} className="spin" /> : <Plus size={14} />} {st === 'added' ? 'Update' : 'Add'}
                  </button>
                )}
                {st === 'skipped' ? (
                  <button className="btn ghost small" onClick={() => skip([p.payee_key], false)}>Undo skip</button>
                ) : st === 'todo' ? (
                  <button className="btn ghost small" onClick={() => skip([p.payee_key])}>
                    <SkipForward size={14} /> Skip
                  </button>
                ) : null}
              </div>
            </div>
          )
        })}
        {!list.length && <div className="empty-step small"><CircleCheckBig size={40} /><b>Nothing here</b></div>}
      </div>
      {list.length > limit && (
        <button className="btn ghost small" style={{ marginTop: 8 }} onClick={() => setLimit(limit + 40)}>Show more ({list.length - limit} left)</button>
      )}
      <label className="check small" style={{ marginTop: 12 }}>
        <input type="checkbox" checked={renameOld} onChange={(e) => setRenameOld(e.target.checked)} /> Also rename matching records already in the app
      </label>
    </>
  )
}

/** Step 4: rows that can't be imported as they are. Fix by hand, or auto-analyse; skipping offers the analysis first. */
function ActionStep({ rows, records, skipped, setSkipped, onEdit, onApply, onError }) {
  const [results, setResults] = useState({}) // row_no → analysis
  const [asking, setAsking] = useState(null) // row_no whose Skip asks "auto-analyse first?"
  const [busy, setBusy] = useState(null)
  const open = rows.filter(([r]) => !skipped.has(r.row_no))
  const done = rows.filter(([r]) => skipped.has(r.row_no))
  const setSkip = (rowNo, on = true) =>
    setSkipped((prev) => {
      const next = new Set(prev)
      on ? next.add(rowNo) : next.delete(rowNo)
      return next
    })

  const analyse = async (rowNos) => {
    setBusy(rowNos.length === 1 ? rowNos[0] : 'all')
    try {
      const res = await api.post('/imports/analyse', { records: records.map(clean), row_nos: rowNos })
      setResults((prev) => ({ ...prev, ...Object.fromEntries(res.data.map((x) => [x.row_no, x])) }))
      return res.data
    } catch (err) {
      onError(errorMessage(err))
      return []
    } finally {
      setBusy(null)
      setAsking(null)
    }
  }
  const apply = (fixes) => {
    onApply(fixes.filter((x) => x.solved))
    setResults((prev) => {
      const next = { ...prev }
      fixes.forEach((x) => delete next[x.row_no])
      return next
    })
  }
  const solvedWaiting = Object.values(results).filter((x) => x.solved && open.some(([r]) => r.row_no === x.row_no))

  if (!rows.length)
    return (
      <div className="empty-step">
        <CircleCheckBig size={56} />
        <b>Every row can be imported</b>
        <span className="muted">Nothing needs your action. Press Next to review and import.</span>
      </div>
    )

  return (
    <>
      <div className="action-summary">
        <span className="action-count"><ListChecks size={30} /> <b>{open.length}</b> to look at · {done.length} skipped</span>
        <span className="spacer" />
        {open.length > 0 && (
          <button className="btn secondary" disabled={!!busy} onClick={() => analyse(open.map(([r]) => r.row_no))}>
            {busy === 'all' ? <RefreshCw size={16} className="spin" /> : <WandSparkles size={16} />} Auto-analyse all {open.length}
          </button>
        )}
        {solvedWaiting.length > 0 && (
          <button className="btn" onClick={() => apply(solvedWaiting)}>
            <Check size={16} /> Apply {solvedWaiting.length} fix(es)
          </button>
        )}
      </div>
      <div className="action-list">
        {open.map(([r, i]) => {
          const res = results[r.row_no]
          return (
            <div key={r.row_no} className="action-card">
              <div className="action-head">
                <span className="badge bad">Row {r.row_no}</span>
                <b className="grow">{r.description || '(no description)'}</b>
                <button className="btn ghost small" disabled={busy === r.row_no} onClick={() => analyse([r.row_no])}>
                  {busy === r.row_no ? <RefreshCw size={14} className="spin" /> : <WandSparkles size={14} />} Auto-analyse
                </button>
                <button className="btn ghost small" onClick={() => setAsking(r.row_no)}>
                  <SkipForward size={14} /> Skip
                </button>
              </div>
              <ul className="action-problems">
                {(r.problems || []).map((p, k) => <li key={k}>{p}</li>)}
              </ul>
              <div className="action-fields">
                <label>Date<DateInput value={r.date || ''} max={todayISO()} onChange={(e) => onEdit(i, { date: e.target.value || null })} /></label>
                <label>Type
                  <select value={r.kind || ''} onChange={(e) => onEdit(i, { kind: e.target.value || null })}>
                    <option value="">Choose…</option>
                    <option value="EXPENSE">Expense (money out)</option>
                    <option value="INCOME">Income (money in)</option>
                    <option value="SPLIT">Split bill</option>
                    <option value="TRANSFER">Transfer</option>
                    <option value="OPENING">Opening balance</option>
                  </select>
                </label>
                <label>Amount<input type="number" step="0.01" min="0" value={r.amount ?? ''} onChange={(e) => onEdit(i, { amount: e.target.value === '' ? null : e.target.value })} /></label>
                <label className="wide">Description<input value={r.description || ''} maxLength={300} onChange={(e) => onEdit(i, { description: e.target.value })} /></label>
              </div>
              {asking === r.row_no && !res && (
                <div className="action-ask">
                  <WandSparkles size={28} />
                  <div className="grow">
                    <b>Before skipping: shall we try to work this row out?</b>
                    <div className="muted small">We look at the balance, the rows around it and the description.</div>
                  </div>
                  <button className="btn" onClick={() => analyse([r.row_no])}>Auto-analyse</button>
                  <button className="btn ghost" onClick={() => { setSkip(r.row_no); setAsking(null) }}>Skip anyway</button>
                </div>
              )}
              {res && (
                <div className={`action-result ${res.solved ? 'ok' : 'bad'}`}>
                  {res.solved ? <CircleCheckBig size={28} /> : <WandSparkles size={28} />}
                  <div className="grow">
                    <b>{res.solved ? 'Worked out' : res.reasons.length ? 'Partly worked out' : 'Couldn’t work it out'}</b>
                    <ul>
                      {res.reasons.map((x, k) => <li key={k}>{x}</li>)}
                      {res.message && <li>{res.message}</li>}
                    </ul>
                  </div>
                  {res.reasons.length > 0 && (
                    <button className="btn" onClick={() => apply([{ ...res, solved: true }])}>{res.solved ? 'Apply' : 'Apply what was found'}</button>
                  )}
                  <button className="btn ghost" onClick={() => { setSkip(r.row_no); setResults((p) => { const n = { ...p }; delete n[r.row_no]; return n }) }}>
                    Skip anyway
                  </button>
                </div>
              )}
            </div>
          )
        })}
        {!open.length && (
          <div className="empty-step small">
            <CircleCheckBig size={44} />
            <b>All rows are fixed or skipped</b>
          </div>
        )}
      </div>
      {done.length > 0 && (
        <details className="skipped-rows">
          <summary>{done.length} skipped row(s): they won’t be imported</summary>
          {done.map(([r]) => (
            <div key={r.row_no} className="skipped-row">
              <span>Row {r.row_no} · {r.description || '(no description)'} · {(r.problems || [])[0]}</span>
              <button className="btn ghost small" onClick={() => setSkip(r.row_no, false)}>Undo skip</button>
            </div>
          ))}
        </details>
      )}
    </>
  )
}

function Row({ r, hasSplit, onEdit, onRemove }) {
  const [cls, label] = STATUS[r.status] || ['info', r.status]
  const notes = [...(r.problems || []), ...(r.warnings || [])]
  const num = (v) => (v === '' ? null : v)
  return (
    <tr className={r.can_import ? '' : r.status === 'IMPORTED_BEFORE' ? 'row-skipped' : 'row-problem'}>
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
        <DateInput value={r.date || ''} max={todayISO()} onChange={(e) => onEdit({ date: e.target.value || null })} />
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

function CategoryMap({ info, options, setOption }) {
  const [open, setOpen] = useState(false)
  const treated = (c) => !!options.category_treatment[c.value.toLowerCase()]
  const unknown = info.category_values.filter((c) => c.expense_rows && !c.known && !treated(c))
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
  const changed = unknown.filter((c) => options.category_map[c.value.toLowerCase()]).length
  return (
    <div style={{ marginTop: 16 }}>
      <div className="toolbar wrap" style={{ padding: 0, border: 0 }}>
        <h4 style={{ margin: 0 }}>Categories (filled in automatically{changed ? ` · ${changed} changed by you` : ''})</h4>
        <span className="spacer" />
        <button className="btn secondary small" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Change categories'}</button>
      </div>
      <p className="hint" style={{ marginTop: 4 }}>
        Rows without a category get the one their payee had before, else one guessed from the description (Swiggy → Food, Uber → Transport…), else “General”.
        Categories the app doesn’t have are matched to a close one or created. Nothing here has to be changed.
      </p>
      {open && (
        <div className="form-grid">
          {unknown.map((c) => {
            const mapped = options.category_map[c.value.toLowerCase()]
            const isNew = mapped && !info.categories.includes(mapped)
            return (
              <div key={c.value}>
                <label>{c.value ? `“${c.value}”` : 'Rows without a category'} · {c.expense_rows} row(s)</label>
                <select value={mapped ? (isNew ? `new:${mapped}` : mapped) : ''} onChange={(e) => choose(c.value, e.target.value.startsWith('new:') ? e.target.value.slice(4) : e.target.value)}>
                  <option value="">{c.automatic ? `Automatic: ${c.automatic}` : 'Automatic (guessed for each row)'}</option>
                  {c.value && c.value.trim() !== c.automatic && <option value={`new:${c.value.trim()}`}>Create “{c.value.trim()}” as a new category</option>}
                  {isNew && mapped !== c.value.trim() && <option value={`new:${mapped}`}>New category “{mapped}”</option>}
                  <option value="__new__">Create a new category with another name…</option>
                  <optgroup label={c.value ? 'Your categories' : 'Use one category for all of them'}>
                    {info.categories.map((k) => (
                      <option key={k} value={k}>{k}</option>
                    ))}
                  </optgroup>
                </select>
              </div>
            )
          })}
        </div>
      )}
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
        Matched to your accounts by name (dots, spaces and capitals ignored) or last 4 digits. Names that don’t match are created as new accounts (Undo removes them); choose one of yours instead if it’s the same account.
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
        Investments, cash withdrawals, money paid for someone and paid back, returns to family… Moving them to an account keeps your bank balances right without counting them as spending or income. Rows without a category (a bank statement) are listed by payee.
      </p>
      {list.length ? (
        <div className="form-grid">
          {list.map((c) => (
            <div key={c.value}>
              <label>
                {c.payee ? `Payee “${c.label}”` : `“${c.value}”`} · {c.rows} row(s){c.suggested_treatment && !chosen(c) ? ' · suggested: move it' : ''}
              </label>
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
        <Alert type="ok">Replaced {result.replaced.filename}: {result.replaced.removed} record(s) were removed.</Alert>
      )}
      {result.replaced?.error && (
        <Alert type="error">
          The new file is in, but removing {result.replaced.filename} stopped ({result.replaced.error}). Open Import history and press
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
    balance: r.balance ?? null, upi_seq: r.upi_seq ?? null,
    include: r.include !== false, parse_errors: r.parse_errors || [], parse_warnings: r.parse_warnings || [], fingerprint: r.fingerprint || null,
  }
}
