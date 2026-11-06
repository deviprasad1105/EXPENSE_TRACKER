import { useCallback, useEffect, useRef, useState } from 'react'
import { FileSpreadsheet, FileText } from 'lucide-react'
import api, { download, errorMessage } from '../api'
import { useAuth } from '../auth'

export const TYPE_LABEL = {
  PAYMENT: 'Payment',
  RECEIPT: 'Receipt',
  CONTRA: 'Contra',
  JOURNAL: 'Journal',
  SALES: 'Sales',
  PURCHASE: 'Purchase',
}
export const TYPE_HINT = {
  PAYMENT: 'Money paid out: credit the cash or bank ledger it came from.',
  RECEIPT: 'Money received: debit the cash or bank ledger it went into.',
  CONTRA: 'Between your own cash and bank ledgers (deposit, withdrawal, transfer).',
  JOURNAL: 'Adjustments: depreciation, outstanding / prepaid expenses, accrued income, TDS, corrections.',
  SALES: 'A sale. Use “GST invoice” to have the tax worked out.',
  PURCHASE: 'A purchase. Use “GST invoice” to have the tax worked out.',
}
export const SOURCE_LABEL = {
  EXPENSE: 'Expense',
  FUND: 'Money received',
  TRANSFER: 'Transfer',
  SUB_CHARGE: 'Subscription',
  SPLIT_BILL: 'Split bill',
  SPLIT_SETTLEMENT: 'Split settlement',
}

/** ₹1,23,456.00 (Indian grouping). Amounts arrive from the API as exact decimal strings. */
export const inr = (v) =>
  Number(v || 0).toLocaleString('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** An amount with its side, e.g. "₹12,000.00 Dr". Zero shows as a dash. */
export const DrCr = ({ amount, side }) =>
  Number(amount) ? (
    <span className="num-nowrap">
      {inr(Math.abs(Number(amount)))} <span className={`side ${side === 'Cr' || side === 'CR' ? 'cr' : 'dr'}`}>{side === 'CR' ? 'Cr' : side === 'DR' ? 'Dr' : side}</span>
    </span>
  ) : (
    <span className="muted">—</span>
  )

/** Debit-positive signed amount → DrCr */
export const Signed = ({ value }) => <DrCr amount={Math.abs(Number(value))} side={Number(value) >= 0 ? 'Dr' : 'Cr'} />

export const Amt = ({ v, blankZero }) => (blankZero && !Number(v) ? <span className="muted">—</span> : <span className="num-nowrap">{inr(v)}</span>)

/** Exact paise from a string amount, for live totals (the server re-checks everything). */
export const paise = (v) => Math.round(Number(v || 0) * 100)

export const fmtDate = (iso) => (iso ? new Date(`${iso}T00:00`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '')

/**
 * Load data whenever `deps` change. A slower, older response never overwrites a newer one
 * (switching the viewed user or the year quickly used to show the previous figures).
 */
export function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [tick, setTick] = useState(0)
  const latest = useRef(0)
  useEffect(() => {
    const id = ++latest.current
    setState((s) => ({ ...s, loading: true, error: null }))
    fn()
      .then((data) => id === latest.current && setState({ data, error: null, loading: false }))
      .catch((err) => id === latest.current && setState({ data: null, error: errorMessage(err), loading: false }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...state, reload }
}

/** The viewed user's books: settings (years, lock date, writable), groups and ledgers. */
export function useBooks(extraDeps = []) {
  const { params } = useAuth()
  const settings = useLoad(() => api.get('/accounting/settings', { params }).then((r) => r.data), [params, ...extraDeps])
  const meta = useLoad(() => api.get('/accounting/meta').then((r) => r.data), [])
  const ledgers = useLoad(() => api.get('/accounting/ledgers', { params }).then((r) => r.data), [params, ...extraDeps])
  return { params, settings, meta, ledgers }
}

export function FySelect({ years, value, onChange }) {
  return (
    <select value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))} aria-label="Financial year">
      {(years || []).map((y) => (
        <option key={y.fy} value={y.fy}>
          FY {y.label}
        </option>
      ))}
    </select>
  )
}

/** Ledgers grouped by their group, for <select>. */
export function LedgerOptions({ ledgers, groups, filter }) {
  const byGroup = {}
  for (const g of ledgers || []) {
    if (filter && !filter(g)) continue
    ;(byGroup[g.group_code] ||= []).push(g)
  }
  return (groups || [])
    .filter((gr) => byGroup[gr.code])
    .map((gr) => (
      <optgroup key={gr.code} label={gr.name}>
        {byGroup[gr.code].map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
            {g.is_active ? '' : ' (inactive)'}
          </option>
        ))}
      </optgroup>
    ))
}

export function ExportButtons({ url, params, name, onError }) {
  const [busy, setBusy] = useState(null)
  const go = async (format) => {
    setBusy(format)
    try {
      await download(url, { ...params, format }, `${name}.${format}`)
    } catch (err) {
      onError?.(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="actions">
      <button className="btn secondary small" onClick={() => go('xlsx')} disabled={!!busy}>
        <FileSpreadsheet size={14} /> {busy === 'xlsx' ? 'Preparing…' : 'Excel'}
      </button>
      <button className="btn secondary small" onClick={() => go('pdf')} disabled={!!busy}>
        <FileText size={14} /> {busy === 'pdf' ? 'Preparing…' : 'PDF'}
      </button>
    </div>
  )
}

export const CASH_BANK = new Set(['BANK_ACCOUNTS', 'CASH_IN_HAND', 'BANK_OD'])
// Investment and money-with-others accounts have ledgers too, but they aren't cash
export const isCashBank = (g) => CASH_BANK.has(g.group_code) || (g.account_id != null && !['INVESTMENTS', 'LOANS_ADVANCES'].includes(g.group_code))
