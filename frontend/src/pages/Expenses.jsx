import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowDownLeft, ArrowUpRight, Check, Lock, Pencil, Search, Trash2, X } from 'lucide-react'
import api, { errorMessage, money, niceDate, todayISO } from '../api'
import { useRecordsChanged } from '../components/QuickAdd'
import { useAuth } from '../auth'
import { accountLabel } from '../components/Accounts'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'
import LearnCategory, { learnable } from '../components/LearnCategory'
import DateInput from '../components/DateInput'

const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const isDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || '')

/** Quick periods for the date filter. */
function presetRange(key) {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  switch (key) {
    case 'this_month':
      return [iso(new Date(y, m, 1)), todayISO()]
    case 'last_month':
      return [iso(new Date(y, m - 1, 1)), iso(new Date(y, m, 0))]
    case 'last_3':
      return [iso(new Date(y, m - 2, 1)), todayISO()]
    case 'this_year':
      return [iso(new Date(y, 0, 1)), todayISO()]
    case 'all':
      return ['', '']
    default:
      return null
  }
}
const PRESETS = [
  ['this_month', 'This month'],
  ['last_month', 'Last month'],
  ['last_3', 'Last 3 months'],
  ['this_year', 'This year'],
  ['all', 'All time'],
]
const TYPES = [
  ['all', 'All'],
  ['sent', 'Sent'],
  ['received', 'Received'],
]

export default function Expenses() {
  const { params, target, user } = useAuth()
  const [search] = useSearchParams()
  // A link can open the list on a period, e.g. /expenses?start=2026-10-01&end=2026-10-31 from the Overview
  const [filters, setFilters] = useState(() => {
    const [s, e] = presetRange('this_month')
    return {
      start: isDate(search.get('start')) ? search.get('start') : s,
      end: isDate(search.get('end')) ? search.get('end') : e,
      type: ['sent', 'received'].includes(search.get('type')) ? search.get('type') : 'all',
      category: '',
      account_id: '',
      search: search.get('search') || '',
    }
  })
  const [expenses, setExpenses] = useState([])
  const [funds, setFunds] = useState([])
  const [categories, setCategories] = useState([])
  const [incomeCategories, setIncomeCategories] = useState([])
  const [offer, setOffer] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [balance, setBalance] = useState(null)
  const [editing, setEditing] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const q = Object.fromEntries(
      Object.entries({ start: filters.start, end: filters.end, search: filters.search, account_id: filters.account_id }).filter(([, v]) => v),
    )
    // A category is either one of money sent ("sent:Food") or of money received ("received:Salary")
    const [catKind, catName] = splitCategory(filters.category)
    const wantSent = filters.type !== 'received' && catKind !== 'received'
    const wantReceived = filters.type !== 'sent' && catKind !== 'sent'
    const byCat = catName ? { category: catName } : {}
    const today = todayISO()
    const [ex, fu, cats, icats, acc, dash] = await Promise.all([
      wantSent ? api.get('/expenses', { params: { ...params, ...q, ...byCat } }) : null,
      wantReceived ? api.get('/funds', { params: { ...params, ...q, ...byCat } }) : null,
      api.get('/expenses/categories', { params }),
      api.get('/funds/categories', { params }),
      api.get('/accounts', { params }),
      api.get('/dashboard', { params: { ...params, start: today, end: today } }),
    ])
    setExpenses(ex?.data || [])
    setFunds(fu?.data || [])
    setCategories(cats.data)
    setIncomeCategories(icats.data)
    setAccounts(acc.data)
    setBalance(dash.data)
  }, [params, filters])

  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])
  useRecordsChanged(useCallback(() => load().catch(() => {}), [load]))

  // One list, newest first: sent (debit) and received (credit)
  const rows = useMemo(() => {
    const out = [
      ...expenses.map((e) => ({ key: `e${e.id}`, kind: 'sent', date: e.expense_date, name: e.item_name, group: e.category, rec: e })),
      ...funds.map((f) => ({ key: `f${f.id}`, kind: 'received', date: f.fund_date, name: f.source, group: f.category || 'Money received', rec: f })),
    ]
    return out.sort((a, b) => (a.date === b.date ? b.rec.id - a.rec.id : a.date < b.date ? 1 : -1))
  }, [expenses, funds])

  const sent = expenses.reduce((s, e) => s + Number(e.amount), 0)
  const received = funds.reduce((s, f) => s + Number(f.amount), 0)
  const net = received - sent
  const byAccount = Object.fromEntries(accounts.map((a) => [a.id, a]))
  const preset =
    PRESETS.find(([k]) => {
      const r = presetRange(k)
      return r[0] === filters.start && r[1] === filters.end
    })?.[0] || 'custom'
  const filtered = filters.type !== 'all' || filters.category || filters.account_id || filters.search
  const writable = target?.writable

  const set = (k) => (e) => setFilters({ ...filters, [k]: e.target.value })
  const setPreset = (k) => {
    const r = presetRange(k)
    if (r) setFilters({ ...filters, start: r[0], end: r[1] })
  }
  const ed = (k) => (ev) => setEditing({ ...editing, [k]: ev.target.value })

  const saveEdit = async () => {
    try {
      const { id, kind, category, note, before } = editing
      if (kind === 'received') {
        await api.put(`/funds/${id}`, { source: editing.source, category, note: note || null })
      } else {
        const { expense_date, item_name, quantity, amount } = editing
        await api.put(`/expenses/${id}`, { expense_date, item_name, category, quantity: Number(quantity), amount: Number(amount), note })
      }
      setEditing(null)
      setMsg({ type: 'ok', text: kind === 'received' ? 'Money received updated' : 'Expense updated' })
      const name = kind === 'received' ? editing.source : editing.item_name
      setOffer(learnable(before, before.category, category) ? { kind, name, payee_key: before.payee_key, alias_id: before.alias_id, category } : null)
      load()
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
    }
  }

  const remove = async (row) => {
    const r = row.rec
    if (!window.confirm(`Delete "${row.name}" (${money(r.amount)} ${row.kind}) on ${row.date}?`)) return
    try {
      await api.delete(row.kind === 'sent' ? `/expenses/${r.id}` : `/funds/${r.id}`)
      const backup = user.role === 'ADMIN' ? ' A backup was saved (Administration → Backups).' : ''
      setMsg({ type: 'ok', text: `${row.kind === 'sent' ? 'Expense' : 'Money received'} deleted.${backup}` })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const periodText = filters.start || filters.end ? `${filters.start ? niceDate(filters.start) : 'the start'} to ${filters.end ? niceDate(filters.end) : 'today'}` : 'all time'

  return (
    <div>
      <PageHeader title="Expenses" subtitle="Every transaction: money sent (expenses) and money received, each with its category. An entry can be edited for 3 days after it was saved; older ones of a payee get a new category through an alias (Settings → Transaction aliases)." />
      <ReadOnlyNotice />
      <Msg msg={msg} />

      {/* ---------- totals for the filtered period ---------- */}
      <div className="glance-tiles">
        <div>
          <span>
            <ArrowDownLeft size={13} /> Received (credit)
          </span>
          <b className="net-pos">{money(received)}</b>
          <small>
            {funds.length} transaction{funds.length === 1 ? '' : 's'}
          </small>
        </div>
        <div>
          <span>
            <ArrowUpRight size={13} /> Sent (debit) · total expenses
          </span>
          <b className="net-neg">{money(sent)}</b>
          <small>
            {expenses.length} transaction{expenses.length === 1 ? '' : 's'}
          </small>
        </div>
        <div>
          <span>Net (received − sent)</span>
          <b className={net < 0 ? 'net-neg' : 'net-pos'}>{money(net)}</b>
          <small>{periodText}</small>
        </div>
        <div>
          <span>Available balance now</span>
          <b className={balance && balance.balance < 0 ? 'net-neg' : ''}>{balance ? money(balance.balance) : '—'}</b>
          <small>all accounts, all time</small>
        </div>
      </div>

      <div className="card flush">
        {/* ---------- filters ---------- */}
        <div className="toolbar" style={{ flexWrap: 'wrap', rowGap: 8 }}>
          <div className="segmented" role="tablist" aria-label="Sent or received">
            {TYPES.map(([k, label]) => (
              <button
                key={k}
                className={filters.type === k ? 'on' : ''}
                onClick={() => {
                  // A category of the other kind no longer applies
                  const [ck] = splitCategory(filters.category)
                  setFilters({ ...filters, type: k, category: k === 'all' || !ck || ck === k ? filters.category : '' })
                }}
                role="tab"
                aria-selected={filters.type === k}
              >
                {label}
              </button>
            ))}
          </div>
          <select value={preset} onChange={(e) => setPreset(e.target.value)} aria-label="Period">
            {PRESETS.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
            <option value="custom" disabled>
              Custom dates
            </option>
          </select>
          <DateInput value={filters.start} max={filters.end || todayISO()} onChange={set('start')} aria-label="From" />
          <span className="muted">to</span>
          <DateInput value={filters.end} min={filters.start || undefined} max={todayISO()} onChange={set('end')} aria-label="To" />
          <select value={filters.category} onChange={set('category')} aria-label="Category">
            <option value="">All categories</option>
            {filters.type !== 'received' && (
              <optgroup label="Money sent">
                {categories.map((c) => (
                  <option key={c} value={`sent:${c}`}>
                    {c}
                  </option>
                ))}
              </optgroup>
            )}
            {filters.type !== 'sent' && (
              <optgroup label="Money received">
                {incomeCategories.map((c) => (
                  <option key={c} value={`received:${c}`}>
                    {c}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          {accounts.length > 0 && (
            <select value={filters.account_id} onChange={set('account_id')} aria-label="Account">
              <option value="">All accounts</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          )}
          <div style={{ position: 'relative' }}>
            <Search size={16} className="muted" style={{ position: 'absolute', left: 10, top: 11 }} />
            <input placeholder="Search description" value={filters.search} onChange={set('search')} style={{ paddingLeft: 32 }} />
          </div>
          {filtered && (
            <button className="btn ghost small" onClick={() => setFilters({ ...filters, type: 'all', category: '', account_id: '', search: '' })}>
              <X size={14} /> Clear filters
            </button>
          )}
          <span className="spacer" />
          <span className="muted">{rows.length} transactions</span>
        </div>
        {offer && (
          <div style={{ margin: '0 16px' }}>
            <LearnCategory
              offer={offer}
              onClose={() => setOffer(null)}
              onDone={(text) => {
                setOffer(null)
                setMsg({ type: 'ok', text })
                load()
              }}
            />
          </div>
        )}

        {/* ---------- transactions ---------- */}
        <div className="table-wrap">
          <table className="table stack">
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Description</th>
                <th>Category</th>
                <th>Account</th>
                <th className="num">Sent (debit)</th>
                <th className="num">Received (credit)</th>
                <th>Status</th>
                {writable && <th></th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const r = row.rec
                const acct = r.account_id && byAccount[r.account_id] ? accountLabel(byAccount[r.account_id]) : '—'
                if (editing?.key === row.key && row.kind === 'received')
                  return (
                    <tr key={row.key} className="editing">
                      <td>{niceDate(row.date, { relative: true })}</td>
                      <td>
                        <TypeBadge kind="received" />
                      </td>
                      <td>
                        <input value={editing.source} onChange={ed('source')} aria-label="Received from" />
                        <input value={editing.note || ''} onChange={ed('note')} placeholder="Note" style={{ marginTop: 4 }} />
                      </td>
                      <td>
                        <select value={editing.category} onChange={ed('category')} aria-label="Income category">
                          {withCurrent(incomeCategories, editing.category).map((c) => (
                            <option key={c}>{c}</option>
                          ))}
                        </select>
                      </td>
                      <td className="muted">{acct}</td>
                      <td />
                      <td className="num strong net-pos">{money(r.amount)}</td>
                      <td colSpan={writable ? 2 : 1}>
                        <div className="actions">
                          <button className="btn small" onClick={saveEdit}>
                            <Check size={14} /> Save
                          </button>
                          <button className="btn secondary small" onClick={() => setEditing(null)}>
                            <X size={14} /> Cancel
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                if (editing?.key === row.key)
                  return (
                    <tr key={row.key} className="editing">
                      <td>
                        <DateInput value={editing.expense_date} max={todayISO()} onChange={ed('expense_date')} />
                      </td>
                      <td>
                        <TypeBadge kind="sent" />
                      </td>
                      <td>
                        <input value={editing.item_name} onChange={ed('item_name')} />
                        <input value={editing.note || ''} onChange={ed('note')} placeholder="Note" style={{ marginTop: 4 }} />
                      </td>
                      <td>
                        <select value={editing.category} onChange={ed('category')} aria-label="Category">
                          {withCurrent(categories, editing.category).map((c) => (
                            <option key={c}>{c}</option>
                          ))}
                        </select>
                      </td>
                      <td className="muted">{acct}</td>
                      <td className="num">
                        <input className="w-amt" type="number" step="0.01" value={editing.amount} onChange={ed('amount')} />
                        <div className="hint">
                          Qty <input className="w-num" type="number" min="1" value={editing.quantity} onChange={ed('quantity')} />
                        </div>
                      </td>
                      <td />
                      <td colSpan={writable ? 2 : 1}>
                        <div className="actions">
                          <button className="btn small" onClick={saveEdit}>
                            <Check size={14} /> Save
                          </button>
                          <button className="btn secondary small" onClick={() => setEditing(null)}>
                            <X size={14} /> Cancel
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                return (
                  <tr key={row.key}>
                    <td data-label="Date" style={{ whiteSpace: 'nowrap' }}>{niceDate(row.date, { relative: true })}</td>
                    <td data-label="Type">
                      <TypeBadge kind={row.kind} />
                    </td>
                    <td data-label="Description" className="strong stack-title" style={{ wordBreak: 'break-word' }}>
                      <span title={r.raw_name && r.raw_name !== row.name ? `Original: ${r.raw_name}` : undefined}>{row.name}</span>{' '}
                      {r.quantity > 1 && <span className="muted small">× {r.quantity}</span>} {r.is_extra && <span className="badge">extra</span>}
                      {r.origin === 'UPI' && <span className="badge info">UPI</span>}
                      {r.alias_id && (
                        <span className="badge" title={`Alias of: ${r.raw_name}`}>
                          alias
                        </span>
                      )}
                      {r.note && (
                        <div className="hint" style={{ fontWeight: 400 }}>
                          {r.note}
                        </div>
                      )}
                    </td>
                    <td data-label="Category">{row.group}</td>
                    <td data-label="Account" className="muted">{acct}</td>
                    <td data-label="Sent" className={`num strong net-neg ${row.kind === 'sent' ? '' : 'stack-hide'}`}>{row.kind === 'sent' ? money(r.amount) : ''}</td>
                    <td data-label="Received" className={`num strong net-pos ${row.kind === 'received' ? '' : 'stack-hide'}`}>{row.kind === 'received' ? money(r.amount) : ''}</td>
                    <td data-label="Status">
                      {row.kind === 'received' ? (
                        <span className="muted small">{r.editable ? 'Editable' : '—'}</span>
                      ) : r.locked ? (
                        <span className="badge" title={`Locked since ${r.lock_at.replace('T', ' ')}`}>
                          <Lock size={12} /> Locked
                        </span>
                      ) : (
                        <span className="badge ok">Editable until {niceDate(r.lock_at.slice(0, 10))}</span>
                      )}
                    </td>
                    {writable && (
                      <td>
                        {(row.kind === 'received' || r.editable) && (
                          <div className="actions">
                            {r.editable && (
                              <button
                                className="btn ghost small"
                                onClick={() => {
                                  setOffer(null)
                                  setEditing(
                                    row.kind === 'sent'
                                      ? { ...r, kind: 'sent', key: row.key, before: r }
                                      : { id: r.id, kind: 'received', key: row.key, source: r.source, category: r.category || 'Money received', note: r.note, before: r },
                                  )
                                }}
                                title="Edit"
                                aria-label={`Edit ${row.name}`}
                              >
                                <Pencil size={14} />
                              </button>
                            )}
                            <button className="btn ghost small" onClick={() => remove(row)} title="Delete" aria-label={`Delete ${row.name}`} style={{ color: 'var(--danger)' }}>
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
              {!rows.length && <EmptyRow cols={writable ? 9 : 8}>No transactions match these filters.</EmptyRow>}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="total-row">
                  <td colSpan={5}>
                    <b>Total</b> <span className="muted">· {rows.length} transactions</span>
                  </td>
                  <td className="num net-neg">{money(sent)}</td>
                  <td className="num net-pos">{money(received)}</td>
                  <td colSpan={writable ? 2 : 1} />
                </tr>
              </tfoot>
            )}
          </table>
        </div>

        {/* ---------- what's left ---------- */}
        <div className="ledger-sum">
          <div>
            <span>Income (money received)</span>
            <b className="net-pos">+ {money(received)}</b>
          </div>
          <div>
            <span>Spent (money sent)</span>
            <b className="net-neg">− {money(sent)}</b>
          </div>
          <div className="result">
            <span>
              Left over for {periodText}
              {filtered && <small className="muted"> (with these filters)</small>}
            </span>
            <b className={net < 0 ? 'net-neg' : 'net-pos'}>{money(net)}</b>
          </div>
          {balance && (
            <div className="muted small">
              Available balance across everything: {money(balance.total_funds)} received − {money(balance.total_expenses)} spent ={' '}
              <b className={balance.balance < 0 ? 'net-neg' : ''}>{money(balance.balance)}</b>. <Link to="/funds">Add money received →</Link>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** "sent:Food" → ['sent', 'Food']; '' → [null, null]. */
function splitCategory(value) {
  if (!value) return [null, null]
  const i = value.indexOf(':')
  return [value.slice(0, i), value.slice(i + 1)]
}

/** The category list, with the record's own category in it even if it was hidden. */
const withCurrent = (list, current) => (current && !list.includes(current) ? [current, ...list] : list)

function TypeBadge({ kind }) {
  return kind === 'sent' ? (
    <span className="badge bad">
      <ArrowUpRight size={12} /> Sent
    </span>
  ) : (
    <span className="badge ok">
      <ArrowDownLeft size={12} /> Received
    </span>
  )
}
