import { Fragment, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Plus, Undo2 } from 'lucide-react'
import api, { errorMessage } from '../api'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'
import { Amt, fmtDate, FySelect, SOURCE_LABEL, TYPE_LABEL, useBooks, useLoad } from './common'
import VoucherForm from './VoucherForm'

export default function Vouchers() {
  const books = useBooks()
  const { params, settings } = books
  const s = settings.data
  const [fy, setFy] = useState(null)
  const [filters, setFilters] = useState({ voucher_type: '', origin: '', q: '', hide_reversed: false })
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState({})
  const [adding, setAdding] = useState(false)
  const [msg, setMsg] = useState(null)
  const year = s?.years.find((y) => y.fy === (fy ?? s.current_fy))

  // Search after typing stops, not on every key
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => ({ ...f, q: query })), 350)
    return () => clearTimeout(t)
  }, [query])
  useEffect(() => setPage(1), [filters, fy, params])

  const list = useLoad(async () => {
    if (!year) return null
    const p = { ...params, start: year.start, end: year.end, page, size: 50, hide_reversed: filters.hide_reversed }
    if (filters.voucher_type) p.voucher_type = filters.voucher_type
    if (filters.origin) p.origin = filters.origin
    if (filters.q) p.q = filters.q
    return (await api.get('/accounting/vouchers', { params: p })).data
  }, [params, year?.fy, filters, page])

  const reverse = async (v) => {
    const why = window.prompt(`Reverse ${TYPE_LABEL[v.voucher_type]} #${v.voucher_no}? A mirror voucher will be posted on ${fmtDate(v.voucher_date)}.\n\nNarration (optional):`, '')
    if (why === null) return
    try {
      await api.post(`/accounting/vouchers/${v.id}/reverse`, { narration: why || null }, { params })
      setMsg({ type: 'ok', text: `${TYPE_LABEL[v.voucher_type]} #${v.voucher_no} reversed` })
      list.reload()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const error = settings.error || list.error
  const writable = s?.writable
  const items = list.data?.items || []
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.size)) : 1

  return (
    <div>
      <PageHeader title="Vouchers" subtitle="Every entry in your books. Expenses, money received, transfers, subscriptions and split bills post here automatically.">
        {s && <FySelect years={s.years} value={fy ?? s.current_fy} onChange={setFy} />}
        {writable && (
          <button className="btn" onClick={() => setAdding(true)} disabled={!books.ledgers.data}>
            <Plus size={16} /> New voucher
          </button>
        )}
      </PageHeader>
      <ReadOnlyNotice />
      <Msg msg={msg} />
      {error && <Msg msg={{ type: 'error', text: error }} />}
      {s?.lock_date && <p className="hint">Books are locked up to {fmtDate(s.lock_date)}. Entries on or before that date can’t change.</p>}

      <div className="card flush">
        <div className="toolbar">
          <select value={filters.voucher_type} onChange={(e) => setFilters({ ...filters, voucher_type: e.target.value })} aria-label="Voucher type">
            <option value="">All types</option>
            {Object.entries(TYPE_LABEL).map(([k, l]) => (
              <option key={k} value={k}>{l}</option>
            ))}
          </select>
          <select value={filters.origin} onChange={(e) => setFilters({ ...filters, origin: e.target.value })} aria-label="Origin">
            <option value="">Manual and automatic</option>
            <option value="manual">Manual only</option>
            <option value="auto">Posted from transactions</option>
          </select>
          <input placeholder="Search narration or invoice no." value={query} onChange={(e) => setQuery(e.target.value)} style={{ minWidth: 220 }} />
          <label className="check">
            <input type="checkbox" checked={filters.hide_reversed} onChange={(e) => setFilters({ ...filters, hide_reversed: e.target.checked })} /> Hide reversed pairs
          </label>
          <span className="spacer" />
          {list.data && <span className="muted small">{list.data.total} vouchers</span>}
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 28 }}></th>
                <th>Date</th>
                <th>Voucher</th>
                <th>Narration</th>
                <th>From</th>
                <th className="num">Amount</th>
                {writable && <th></th>}
              </tr>
            </thead>
            <tbody>
              {items.map((v) => (
                <Fragment key={v.id}>
                  <tr className={v.status === 'REVERSED' || v.is_reversal ? 'dim' : ''}>
                    <td>
                      <button className="btn ghost small" onClick={() => setOpen((o) => ({ ...o, [v.id]: !o[v.id] }))} aria-label="Show lines">
                        {open[v.id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </button>
                    </td>
                    <td>{fmtDate(v.voucher_date)}</td>
                    <td className="strong">
                      {TYPE_LABEL[v.voucher_type]} #{v.voucher_no}
                      {v.invoice_no && <span className="muted small"> · Inv {v.invoice_no}</span>}
                      {v.is_reversal && <span className="badge warn" style={{ marginLeft: 6 }}>Reversal</span>}
                      {v.status === 'REVERSED' && <span className="badge" style={{ marginLeft: 6 }}>Reversed</span>}
                    </td>
                    <td className="muted">{v.narration}</td>
                    <td className="muted small">{v.source_type ? `${SOURCE_LABEL[v.source_type] || v.source_type}` : 'Manual'}</td>
                    <td className="num strong"><Amt v={v.total} /></td>
                    {writable && (
                      <td>
                        {!v.source_type && !v.is_reversal && v.status === 'POSTED' && (
                          <button className="btn ghost small" title="Reverse" onClick={() => reverse(v)}>
                            <Undo2 size={14} />
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                  {open[v.id] && (
                    <tr className="sub-row">
                      <td></td>
                      <td colSpan={writable ? 6 : 5}>
                        <table className="mini-table voucher-lines">
                          <tbody>
                            {v.lines.map((l) => (
                              <tr key={l.id}>
                                <td>{Number(l.debit) ? `${l.ledger_name} Dr` : <span style={{ paddingLeft: 24 }}>To {l.ledger_name}</span>}</td>
                                <td className="num">{Number(l.debit) ? <Amt v={l.debit} /> : ''}</td>
                                <td className="num">{Number(l.credit) ? <Amt v={l.credit} /> : ''}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {v.source_type && (
                          <p className="hint">Posted from {SOURCE_LABEL[v.source_type]?.toLowerCase()} #{v.source_id}. Edit or delete that transaction and the books follow.</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {!list.loading && !items.length && <EmptyRow cols={writable ? 7 : 6}>No vouchers for these filters.</EmptyRow>}
              {list.loading && <EmptyRow cols={writable ? 7 : 6}>Loading…</EmptyRow>}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="toolbar" style={{ borderBottom: 0, justifyContent: 'flex-end' }}>
            <button className="btn secondary small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="muted small">Page {page} of {pages}</span>
            <button className="btn secondary small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>

      {adding && (
        <VoucherForm
          books={books}
          params={params}
          onClose={() => setAdding(false)}
          onSaved={(v) => {
            setAdding(false)
            setMsg({ type: 'ok', text: `${TYPE_LABEL[v.voucher_type]} #${v.voucher_no} posted` })
            list.reload()
            books.ledgers.reload()
          }}
        />
      )}
    </div>
  )
}
