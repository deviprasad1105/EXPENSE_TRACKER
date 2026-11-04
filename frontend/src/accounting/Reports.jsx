import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import api, { todayISO } from '../api'
import { Msg, PageHeader } from '../components/ui'
import { ExportButtons, FySelect, LedgerOptions, TYPE_LABEL, useBooks, useLoad } from './common'
import { BalanceSheetView, CashFlowView, JournalView, LedgerView, ProfitLossView, TradingView, TrialBalanceView } from './ReportViews'

export const REPORTS = [
  ['journal', 'Journal'],
  ['ledger', 'General Ledger'],
  ['trial-balance', 'Trial Balance'],
  ['trading', 'Trading Account'],
  ['profit-loss', 'Profit & Loss'],
  ['balance-sheet', 'Balance Sheet'],
  ['cash-flow', 'Cash Flow'],
]
const RANGED = new Set(['journal', 'ledger', 'trial-balance'])
const TO_DATE = new Set(['trading', 'profit-loss', 'cash-flow'])

export default function AccountingReports() {
  const books = useBooks()
  const { params, settings, meta, ledgers } = books
  const [search, setSearch] = useSearchParams()
  const tab = REPORTS.some(([k]) => k === search.get('r')) ? search.get('r') : 'trial-balance'
  const s = settings.data
  const [fy, setFy] = useState(null)
  const [range, setRange] = useState({ start: '', end: '' })
  const [asOf, setAsOf] = useState(todayISO())
  const [ledgerId, setLedgerId] = useState(search.get('ledger') || '')
  const [voucherType, setVoucherType] = useState('')
  const [tbView, setTbView] = useState('grouped')
  const [msg, setMsg] = useState(null)
  const year = s?.years.find((y) => y.fy === (fy ?? s.current_fy))

  // A new year resets the dates to that year
  useEffect(() => {
    if (year) setRange({ start: year.start, end: year.end < todayISO() ? year.end : todayISO() })
  }, [year?.fy])

  const query = (() => {
    if (!year) return null
    if (tab === 'balance-sheet') return { as_of: asOf }
    if (TO_DATE.has(tab)) return { fy: year.fy, end: range.end || undefined }
    const q = { fy: year.fy, start: range.start || undefined, end: range.end || undefined }
    if (tab === 'ledger') {
      if (!ledgerId) return null
      q.ledger_id = ledgerId
    }
    if (tab === 'journal' && voucherType) q.voucher_type = voucherType
    if (tab === 'trial-balance') q.view = tbView
    return q
  })()
  const key = JSON.stringify(query)
  // Each result remembers which report it belongs to: while another tab loads, the old data is never shown in the new view
  const report = useLoad(async () => (query ? { tab, body: (await api.get(`/accounting/reports/${tab}`, { params: { ...params, ...query } })).data } : null), [tab, key, params])
  const data = report.data?.tab === tab ? report.data.body : null

  const openLedger = (id) => {
    setLedgerId(String(id))
    setSearch({ r: 'ledger', ledger: String(id) })
  }

  const view = data && {
    journal: <JournalView r={data} />,
    ledger: <LedgerView r={data} />,
    'trial-balance': <TrialBalanceView r={data} grouped={tbView === 'grouped'} onLedger={openLedger} />,
    trading: <TradingView r={data} onLedger={openLedger} />,
    'profit-loss': <ProfitLossView r={data} onLedger={openLedger} />,
    'balance-sheet': <BalanceSheetView r={data} onLedger={openLedger} />,
    'cash-flow': <CashFlowView r={data} />,
  }[tab]

  return (
    <div>
      <PageHeader title="Accounting reports" subtitle={s ? `${s.business_name || ''}${s.gstin ? ` · GSTIN ${s.gstin}` : ''}` : ''} />
      <div className="segmented report-tabs" role="tablist" style={{ marginBottom: 16 }}>
        {REPORTS.map(([k, label]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setSearch({ r: k })} role="tab" aria-selected={tab === k}>
            {label}
          </button>
        ))}
      </div>
      <Msg msg={msg} />
      {settings.error && <Msg msg={{ type: 'error', text: settings.error }} />}

      <div className="card flush">
        <div className="toolbar">
          {tab === 'balance-sheet' ? (
            <label className="filters">
              As at <input type="date" value={asOf} max={todayISO()} onChange={(e) => setAsOf(e.target.value)} />
            </label>
          ) : (
            <>
              {s && <FySelect years={s.years} value={fy ?? s.current_fy} onChange={setFy} />}
              {RANGED.has(tab) && (
                <label className="filters">
                  From <input type="date" value={range.start} min={year?.start} max={year?.end} onChange={(e) => setRange({ ...range, start: e.target.value })} />
                </label>
              )}
              <label className="filters">
                {RANGED.has(tab) ? 'to' : 'Up to'} <input type="date" value={range.end} min={year?.start} max={year?.end} onChange={(e) => setRange({ ...range, end: e.target.value })} />
              </label>
            </>
          )}
          {tab === 'ledger' && (
            <select value={ledgerId} onChange={(e) => { setLedgerId(e.target.value); setSearch({ r: 'ledger', ledger: e.target.value }) }} aria-label="Ledger">
              <option value="">Choose a ledger…</option>
              <LedgerOptions ledgers={ledgers.data} groups={meta.data?.groups} />
            </select>
          )}
          {tab === 'journal' && (
            <select value={voucherType} onChange={(e) => setVoucherType(e.target.value)} aria-label="Voucher type">
              <option value="">All vouchers</option>
              {Object.entries(TYPE_LABEL).map(([k, l]) => (
                <option key={k} value={k}>{l}</option>
              ))}
            </select>
          )}
          {tab === 'trial-balance' && (
            <div className="segmented">
              <button className={tbView === 'grouped' ? 'on' : ''} onClick={() => setTbView('grouped')}>Grouped</button>
              <button className={tbView === 'ledgers' ? 'on' : ''} onClick={() => setTbView('ledgers')}>Ledger-wise</button>
            </div>
          )}
          <span className="spacer" />
          {query && data && (
            <ExportButtons url={`/accounting/reports/${tab}`} params={{ ...params, ...query }} name={tab} onError={(text) => setMsg({ type: 'error', text })} />
          )}
        </div>
        <div className="report-body">
          {tab === 'ledger' && !ledgerId && <p className="muted">Choose a ledger to see its entries.</p>}
          {report.error && <Msg msg={{ type: 'error', text: report.error }} />}
          {report.loading && !data && <p className="muted">Loading…</p>}
          {data && (
            <>
              <div className="report-title">
                <h2>{REPORTS.find(([k]) => k === tab)[1]}</h2>
                <span className="muted">{data.period}</span>
              </div>
              {data.closing_stock_missing ? (
                <Msg msg={{ type: 'warn', text: 'Closing stock for this year hasn’t been entered (Accounting settings → Stock). It is taken as zero.' }} />
              ) : null}
              {view}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
