import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { EmptyRow } from '../components/ui'
import { Amt, DrCr, fmtDate, Signed, TYPE_LABEL } from './common'

const LedgerName = ({ item, onLedger }) =>
  item.ledger_id && onLedger ? (
    <button className="link-btn" onClick={() => onLedger(item.ledger_id)} title="Open ledger">
      {item.name}
    </button>
  ) : (
    item.name
  )

// ---------- Journal ----------
export function JournalView({ r }) {
  return (
    <div className="table-wrap">
      <table className="table report-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Voucher</th>
            <th>Particulars</th>
            <th className="num">Debit</th>
            <th className="num">Credit</th>
          </tr>
        </thead>
        <tbody>
          {r.entries.map((e) => (
            <Fragment key={e.id}>
              {e.lines.map((l, i) => (
                <tr key={i} className={`${i === 0 ? 'entry-first' : 'entry-cont'} ${e.status === 'REVERSED' || e.is_reversal ? 'dim' : ''}`}>
                  <td>{i === 0 ? fmtDate(e.date) : ''}</td>
                  <td>
                    {i === 0 && (
                      <>
                        {TYPE_LABEL[e.voucher_type]} #{e.voucher_no}
                        {e.is_reversal && <span className="badge warn" style={{ marginLeft: 6 }}>Reversal</span>}
                        {e.status === 'REVERSED' && <span className="badge" style={{ marginLeft: 6 }}>Reversed</span>}
                      </>
                    )}
                  </td>
                  <td>{Number(l.debit) ? <b>{l.ledger} Dr</b> : <span style={{ paddingLeft: 28 }}>To {l.ledger}</span>}</td>
                  <td className="num">{Number(l.debit) ? <Amt v={l.debit} /> : ''}</td>
                  <td className="num">{Number(l.credit) ? <Amt v={l.credit} /> : ''}</td>
                </tr>
              ))}
              {e.narration && (
                <tr className="entry-cont narration">
                  <td></td>
                  <td></td>
                  <td colSpan={3} className="muted small">({e.narration})</td>
                </tr>
              )}
            </Fragment>
          ))}
          {!r.entries.length && <EmptyRow cols={5}>No vouchers in this period.</EmptyRow>}
        </tbody>
        <tfoot>
          <tr className="total-row">
            <td colSpan={3}>Total</td>
            <td className="num"><Amt v={r.total_debit} /></td>
            <td className="num"><Amt v={r.total_credit} /></td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

// ---------- General Ledger ----------
export function LedgerView({ r }) {
  return (
    <>
      <p className="muted">{r.ledger.group}</p>
      <div className="table-wrap">
        <table className="table report-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Particulars</th>
              <th>Voucher</th>
              <th className="num">Debit</th>
              <th className="num">Credit</th>
              <th className="num">Balance</th>
            </tr>
          </thead>
          <tbody>
            <tr className="strong-row">
              <td></td>
              <td>Opening balance</td>
              <td></td>
              <td></td>
              <td></td>
              <td className="num"><DrCr amount={r.opening} side={r.opening_side} /></td>
            </tr>
            {r.entries.map((e, i) => (
              <tr key={i} className={e.status === 'REVERSED' || e.is_reversal ? 'dim' : ''}>
                <td>{fmtDate(e.date)}</td>
                <td>
                  {e.particulars}
                  {e.others.length > 1 && <span className="muted small"> ({e.others.join(', ')})</span>}
                  {e.narration && <div className="muted small">{e.narration}</div>}
                </td>
                <td className="muted">{TYPE_LABEL[e.voucher_type]} #{e.voucher_no}</td>
                <td className="num">{Number(e.debit) ? <Amt v={e.debit} /> : ''}</td>
                <td className="num">{Number(e.credit) ? <Amt v={e.credit} /> : ''}</td>
                <td className="num"><DrCr amount={e.balance} side={e.balance_side} /></td>
              </tr>
            ))}
            {!r.entries.length && <EmptyRow cols={6}>No entries in this period.</EmptyRow>}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <td></td>
              <td>Total</td>
              <td></td>
              <td className="num"><Amt v={r.total_debit} /></td>
              <td className="num"><Amt v={r.total_credit} /></td>
              <td></td>
            </tr>
            <tr className="total-row">
              <td></td>
              <td>Closing balance</td>
              <td colSpan={3}></td>
              <td className="num"><DrCr amount={r.closing} side={r.closing_side} /></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  )
}

// ---------- Trial Balance ----------
function TbCells({ x }) {
  const c = Number(x.closing)
  return (
    <>
      <td className="num"><Signed value={x.opening} /></td>
      <td className="num"><Amt v={x.debit} blankZero /></td>
      <td className="num"><Amt v={x.credit} blankZero /></td>
      <td className="num">{c > 0 ? <Amt v={c} /> : ''}</td>
      <td className="num">{c < 0 ? <Amt v={-c} /> : ''}</td>
    </>
  )
}

export function TrialBalanceView({ r, grouped, onLedger }) {
  const [open, setOpen] = useState({})
  const t = r.totals
  return (
    <>
      <span className={`badge ${r.balanced ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
        {r.balanced ? 'Balanced: debits equal credits' : 'Not balanced'}
      </span>
      <div className="table-wrap">
        <table className="table report-table">
          <thead>
            <tr>
              <th>Particulars</th>
              <th className="num">Opening</th>
              <th className="num">Debit</th>
              <th className="num">Credit</th>
              <th className="num">Closing Dr</th>
              <th className="num">Closing Cr</th>
            </tr>
          </thead>
          <tbody>
            {grouped
              ? r.groups.map((g) => {
                  const single = g.ledgers.length === 1 && g.ledgers[0].name === g.name
                  return (
                    <Fragment key={g.code}>
                      <tr className="group-row clickable" onClick={() => !single && setOpen((o) => ({ ...o, [g.code]: !o[g.code] }))}>
                        <td>
                          {!single && (open[g.code] ? <ChevronDown size={14} /> : <ChevronRight size={14} />)} {g.name}
                        </td>
                        <TbCells x={g} />
                      </tr>
                      {open[g.code] &&
                        g.ledgers.map((l) => (
                          <tr key={`${g.code}-${l.ledger_id ?? l.name}`}>
                            <td style={{ paddingLeft: 40 }}>
                              <LedgerName item={l} onLedger={onLedger} />
                              <span className="muted small"> · {l.group}</span>
                            </td>
                            <TbCells x={l} />
                          </tr>
                        ))}
                    </Fragment>
                  )
                })
              : r.rows.map((l) => (
                  <tr key={l.ledger_id ?? l.name}>
                    <td>
                      <LedgerName item={l} onLedger={onLedger} />
                      <span className="muted small"> · {l.group}</span>
                    </td>
                    <TbCells x={l} />
                  </tr>
                ))}
            {!r.rows.length && <EmptyRow cols={6}>Nothing posted yet.</EmptyRow>}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <td>Grand total</td>
              <td className="num muted small">
                Dr <Amt v={t.opening_debit} /> · Cr <Amt v={t.opening_credit} />
              </td>
              <td className="num"><Amt v={t.debit} /></td>
              <td className="num"><Amt v={t.credit} /></td>
              <td className="num"><Amt v={t.closing_debit} /></td>
              <td className="num"><Amt v={t.closing_credit} /></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  )
}

// ---------- T-format statements ----------
function Side({ title, sections, extra = [], total, onLedger }) {
  return (
    <div className="t-side">
      <div className="t-head">
        <span>{title}</span>
        <span>Amount</span>
      </div>
      {sections
        .filter((s) => s.items.length)
        .map((s) => {
          const single = s.items.length === 1 && s.items[0].name === s.title
          return (
            <div key={s.title} className="t-section">
              <div className="t-row t-group">
                <span>{s.title}</span>
                <Amt v={s.total} />
              </div>
              {!single &&
                s.items.map((i, k) => (
                  <div key={k} className="t-row t-item">
                    <span><LedgerName item={i} onLedger={onLedger} /></span>
                    <span className="muted"><Amt v={i.amount} /></span>
                  </div>
                ))}
            </div>
          )
        })}
      {extra.map(([label, value]) => (
        <div key={label} className="t-row t-group t-balancing">
          <span>{label}</span>
          <Amt v={value} />
        </div>
      ))}
      <div className="t-row t-total">
        <span>Total</span>
        <Amt v={total} />
      </div>
    </div>
  )
}

export function TradingView({ r, onLedger }) {
  const t = r.trading
  const gp = Number(t.gross_profit)
  return (
    <>
      <div className="t-account">
        <Side title="Particulars (Dr)" sections={t.debit} extra={gp > 0 ? [['Gross Profit c/o', gp]] : []} total={t.total} onLedger={onLedger} />
        <Side title="Particulars (Cr)" sections={t.credit} extra={gp < 0 ? [['Gross Loss c/o', -gp]] : []} total={t.total} onLedger={onLedger} />
      </div>
      <p className="result-line">
        {gp >= 0 ? 'Gross profit' : 'Gross loss'}: <b><Amt v={Math.abs(gp)} /></b>
      </p>
    </>
  )
}

export function ProfitLossView({ r, onLedger }) {
  const pl = r.profit_loss
  const gp = Number(pl.gross_profit)
  const net = Number(pl.net_profit)
  const left = [...(gp < 0 ? [{ title: 'Gross Loss b/f', items: [{ name: 'Gross Loss b/f', amount: -gp }], total: -gp }] : []), ...pl.debit]
  const right = [...(gp > 0 ? [{ title: 'Gross Profit b/f', items: [{ name: 'Gross Profit b/f', amount: gp }], total: gp }] : []), ...pl.credit]
  return (
    <>
      <div className="t-account">
        <Side title="Particulars (Dr)" sections={left} extra={net > 0 ? [['Net Profit', net]] : []} total={pl.total} onLedger={onLedger} />
        <Side title="Particulars (Cr)" sections={right} extra={net < 0 ? [['Net Loss', -net]] : []} total={pl.total} onLedger={onLedger} />
      </div>
      <p className="result-line">
        {net >= 0 ? 'Net profit' : 'Net loss'}: <b><Amt v={Math.abs(net)} /></b>
        <span className="muted small"> · Drawings (personal spending) reduce capital in the Balance Sheet, not this profit.</span>
      </p>
    </>
  )
}

export function BalanceSheetView({ r, onLedger }) {
  return (
    <>
      <span className={`badge ${r.balanced ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
        {r.balanced ? 'Balanced: liabilities equal assets' : 'Not balanced'}
      </span>
      <div className="t-account">
        <Side title="Liabilities" sections={r.liabilities} total={r.total_liabilities} onLedger={onLedger} />
        <Side title="Assets" sections={r.assets} total={r.total_assets} onLedger={onLedger} />
      </div>
    </>
  )
}

// ---------- Cash Flow ----------
export function CashFlowView({ r }) {
  return (
    <>
      <span className={`badge ${r.reconciled ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
        {r.reconciled ? 'Reconciled with cash and bank balances' : 'Does not reconcile'}
      </span>
      <div className="table-wrap">
        <table className="table report-table">
          <thead>
            <tr>
              <th>Particulars</th>
              <th className="num">Amount</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {r.sections.map((s) => (
              <Fragment key={s.title}>
                <tr className="group-row">
                  <td colSpan={3}>{s.title}</td>
                </tr>
                {s.items.map((i) => (
                  <tr key={i.name}>
                    <td style={{ paddingLeft: 32 }}>{i.name}</td>
                    <td className="num"><Amt v={i.amount} /></td>
                    <td></td>
                  </tr>
                ))}
                <tr className="strong-row">
                  <td style={{ paddingLeft: 32 }}>Net cash from {s.title.split('from ')[1]}</td>
                  <td></td>
                  <td className="num"><Amt v={s.total} /></td>
                </tr>
              </Fragment>
            ))}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <td>Net increase / (decrease) in cash and cash equivalents</td>
              <td></td>
              <td className="num"><Amt v={r.net_change} /></td>
            </tr>
            <tr>
              <td>Cash and cash equivalents at the beginning</td>
              <td></td>
              <td className="num"><Amt v={r.opening_cash} /></td>
            </tr>
            <tr className="total-row">
              <td>Cash and cash equivalents at the end</td>
              <td></td>
              <td className="num"><Amt v={r.closing_cash} /></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  )
}
