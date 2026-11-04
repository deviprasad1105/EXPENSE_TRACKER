import { useEffect, useState } from 'react'
import { Lock, LockOpen, RefreshCw, Save } from 'lucide-react'
import api, { errorMessage, todayISO } from '../api'
import ReadOnlyNotice from '../components/ReadOnlyNotice'
import { EmptyRow, Msg, PageHeader } from '../components/ui'
import { fmtDate, inr, LedgerOptions, useBooks, useLoad } from './common'

const RULE_TARGETS = new Set(['CAPITAL', 'RESERVES', 'SALES', 'PURCHASE', 'DIRECT_INCOMES', 'DIRECT_EXPENSES', 'INDIRECT_INCOMES',
  'INDIRECT_EXPENSES', 'FIXED_ASSETS', 'LOANS_ADVANCES', 'SUNDRY_DEBTORS', 'SUNDRY_CREDITORS', 'CURRENT_LIABILITIES', 'PROVISIONS', 'SUSPENSE'])

export default function AccountingSettings() {
  const [version, setVersion] = useState(0)
  const books = useBooks([version])
  const { params, settings, meta, ledgers } = books
  const s = settings.data
  const [tab, setTab] = useState('business')
  const [msg, setMsg] = useState(null)
  const writable = s?.writable
  const changed = (text) => {
    setMsg({ type: 'ok', text })
    setVersion((v) => v + 1)
  }
  const failed = (err) => setMsg({ type: 'error', text: errorMessage(err) })

  return (
    <div>
      <PageHeader title="Accounting settings" subtitle={s ? `Books from ${fmtDate(s.books_start)} · financial year April to March` : ''} />
      <ReadOnlyNotice />
      <Msg msg={msg} />
      {settings.error && <Msg msg={{ type: 'error', text: settings.error }} />}
      <div className="segmented" style={{ marginBottom: 16 }}>
        {[['business', 'Business & lock'], ['rules', 'Posting rules'], ['stock', 'Stock'], ['sync', 'Re-sync']].map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {s && tab === 'business' && <Business s={s} states={meta.data?.states || []} params={params} writable={writable} onSaved={changed} onError={failed} />}
      {s && tab === 'rules' && <Rules params={params} ledgers={ledgers.data} groups={meta.data?.groups} writable={writable} onSaved={changed} onError={failed} version={version} />}
      {s && tab === 'stock' && <Stock params={params} writable={writable} onSaved={changed} onError={failed} version={version} />}
      {s && tab === 'sync' && <Resync s={s} params={params} writable={writable} onSaved={changed} onError={failed} />}
    </div>
  )
}

function Business({ s, states, params, writable, onSaved, onError }) {
  const [form, setForm] = useState({ business_name: s.business_name || '', state: s.state || '', gstin: s.gstin || '' })
  const [lockDate, setLockDate] = useState(s.lock_date || '')
  useEffect(() => setLockDate(s.lock_date || ''), [s.lock_date])

  const save = async (e) => {
    e.preventDefault()
    try {
      await api.put('/accounting/settings', { ...form, gstin: form.gstin || null }, { params })
      onSaved('Business details saved')
    } catch (err) {
      onError(err)
    }
  }
  const lock = async () => {
    if (!lockDate) return
    if (!window.confirm(`Lock the books up to ${fmtDate(lockDate)}?\n\nNothing dated on or before it can then be posted, edited, deleted or reversed, not even by an admin. Corrections go in as vouchers in an open period.`)) return
    try {
      await api.put('/accounting/settings', { lock_date: lockDate }, { params })
      onSaved(`Books locked up to ${fmtDate(lockDate)}`)
    } catch (err) {
      onError(err)
    }
  }
  const unlock = async () => {
    if (!window.confirm('Remove the lock? Entries in the earlier period can then be changed again.')) return
    try {
      await api.put('/accounting/settings', { clear_lock: true }, { params })
      onSaved('Lock removed')
    } catch (err) {
      onError(err)
    }
  }

  return (
    <div className="grid-2">
      <form className="card" onSubmit={save}>
        <div className="card-head">
          <div>
            <h3>Business</h3>
            <p>Shown on reports. The state decides CGST + SGST (same state) or IGST (other state) on invoices.</p>
          </div>
        </div>
        <div className="form-grid">
          <div className="span-all">
            <label>Business name</label>
            <input value={form.business_name} maxLength={120} onChange={(e) => setForm({ ...form, business_name: e.target.value })} disabled={!writable} />
          </div>
          <div>
            <label>State</label>
            <select value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} disabled={!writable}>
              <option value="">—</option>
              {states.map((st) => (
                <option key={st}>{st}</option>
              ))}
            </select>
          </div>
          <div>
            <label>GSTIN</label>
            <input value={form.gstin} maxLength={15} placeholder="27ABCDE1234F1Z5" onChange={(e) => setForm({ ...form, gstin: e.target.value.toUpperCase() })} disabled={!writable} />
          </div>
        </div>
        {writable && (
          <div className="form-actions" style={{ marginTop: 14 }}>
            <button className="btn"><Save size={16} /> Save</button>
          </div>
        )}
      </form>
      <div className="card">
        <div className="card-head">
          <div>
            <h3>Period lock</h3>
            <p>After filing or closing a period, lock it so the figures can’t change.</p>
          </div>
        </div>
        {s.lock_date ? (
          <div className="alert warn">
            <Lock size={18} />
            <div className="grow">
              Locked up to <b>{fmtDate(s.lock_date)}</b>
              {s.locked_by && <span className="muted small"> · by @{s.locked_by} on {fmtDate(s.locked_at?.slice(0, 10))}</span>}
            </div>
          </div>
        ) : (
          <p className="muted">Not locked.</p>
        )}
        {writable && (
          <div className="form-grid" style={{ marginTop: 12 }}>
            <div>
              <label>Lock up to</label>
              <input type="date" value={lockDate} max={todayISO()} onChange={(e) => setLockDate(e.target.value)} />
            </div>
            <div className="form-actions">
              {s.lock_date && (
                <button type="button" className="btn secondary" onClick={unlock}><LockOpen size={16} /> Unlock</button>
              )}
              <button type="button" className="btn" onClick={lock} disabled={!lockDate || lockDate === s.lock_date}><Lock size={16} /> Lock</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Rules({ params, ledgers, groups, writable, onSaved, onError, version }) {
  const rules = useLoad(async () => (await api.get('/accounting/rules', { params })).data, [params, version])
  const change = async (rule, ledgerId) => {
    try {
      const res = await api.put(`/accounting/rules/${rule.id}`, { ledger_id: Number(ledgerId) }, { params })
      const sync = res.data.sync
      const locked = sync.locked.length ? ` ${sync.locked.length} in the locked period kept their old ledger.` : ''
      onSaved(`“${rule.label}” now posts to the new ledger: ${sync.reposted} transaction(s) re-posted.${locked}`)
    } catch (err) {
      onError(err)
    }
  }
  const targets = (ledgers || []).filter((g) => g.is_active && !g.account_id && !g.split_group_id && RULE_TARGETS.has(g.group_code))
  const section = (kind, title, hint) => {
    const list = (rules.data || []).filter((r) => r.kind === kind)
    return (
      <div className="card flush" key={kind}>
        <div className="card-head" style={{ padding: '16px 24px 0' }}>
          <div>
            <h3>{title}</h3>
            <p>{hint}</p>
          </div>
        </div>
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table">
            <thead>
              <tr>
                <th>{kind === 'FUND_SOURCE' ? 'Source' : 'Category'}</th>
                <th className="num">Transactions</th>
                <th>Posts to</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id}>
                  <td className="strong">{r.label}</td>
                  <td className="num">{r.usage}</td>
                  <td>
                    {writable ? (
                      <select value={r.ledger_id} onChange={(e) => change(r, e.target.value)} aria-label={`Ledger for ${r.label}`}>
                        <LedgerOptions ledgers={targets} groups={groups} />
                      </select>
                    ) : (
                      <span>{r.ledger_name}</span>
                    )}
                    <span className="muted small"> · {r.group_name}</span>
                  </td>
                </tr>
              ))}
              {!list.length && <EmptyRow cols={3}>Nothing yet: rules appear as transactions are posted.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </div>
    )
  }
  return (
    <>
      {rules.error && <Msg msg={{ type: 'error', text: rules.error }} />}
      {section('EXPENSE_CATEGORY', 'Expense categories', 'Personal spending goes to Drawings (it reduces your capital, not the business’s profit). Point business categories at their expense ledger; past entries in open periods are re-posted.')}
      {section('FUND_SOURCE', 'Money received', 'Personal money coming in (salary, gifts) is capital introduced. Point business income at Sales or an income ledger.')}
    </>
  )
}

function Stock({ params, writable, onSaved, onError, version }) {
  const years = useLoad(async () => (await api.get('/accounting/stock', { params })).data, [params, version])
  const [edit, setEdit] = useState({})
  const save = async (y) => {
    const e = edit[y.fy] || {}
    const body = { fy: y.fy }
    if (y.opening_editable && e.opening !== undefined) body.opening_value = e.opening || '0'
    if (e.closing !== undefined) {
      if (e.closing === '') body.clear_closing = true
      else body.closing_value = e.closing
    }
    try {
      await api.put('/accounting/stock', body, { params })
      setEdit({ ...edit, [y.fy]: undefined })
      onSaved(`Stock for FY ${y.label} saved`)
    } catch (err) {
      onError(err)
    }
  }
  return (
    <div className="card flush">
      <div className="card-head" style={{ padding: '16px 24px 0' }}>
        <div>
          <h3>Stock in hand</h3>
          <p>Enter the value of closing stock at the end of each year (from your physical count). It goes into the Trading Account and the Balance Sheet. Each year opens with the previous year’s closing stock.</p>
        </div>
      </div>
      {years.error && <Msg msg={{ type: 'error', text: years.error }} />}
      <div className="table-wrap" style={{ marginTop: 12 }}>
        <table className="table">
          <thead>
            <tr>
              <th>Financial year</th>
              <th className="num">Opening stock</th>
              <th className="num">Closing stock</th>
              {writable && <th></th>}
            </tr>
          </thead>
          <tbody>
            {(years.data || []).map((y) => {
              const e = edit[y.fy] || {}
              return (
                <tr key={y.fy}>
                  <td className="strong">
                    FY {y.label} {y.locked && <span className="badge warn">Locked</span>}
                  </td>
                  <td className="num">
                    {writable && y.opening_editable && !y.locked ? (
                      <input type="number" min="0" step="0.01" className="w-amt" value={e.opening ?? y.opening} onChange={(ev) => setEdit({ ...edit, [y.fy]: { ...e, opening: ev.target.value } })} />
                    ) : (
                      inr(y.opening)
                    )}
                  </td>
                  <td className="num">
                    {writable && !y.locked ? (
                      <input type="number" min="0" step="0.01" className="w-amt" placeholder="Not entered" value={e.closing ?? y.closing ?? ''} onChange={(ev) => setEdit({ ...edit, [y.fy]: { ...e, closing: ev.target.value } })} />
                    ) : y.closing == null ? (
                      <span className="muted">Not entered</span>
                    ) : (
                      inr(y.closing)
                    )}
                  </td>
                  {writable && (
                    <td>
                      {(e.opening !== undefined || e.closing !== undefined) && (
                        <button className="btn small" onClick={() => save(y)}><Save size={14} /> Save</button>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Resync({ s, params, writable, onSaved, onError }) {
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const res = await api.post('/accounting/sync', null, { params })
      setResult(res.data)
      onSaved('Books re-synced')
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3>Re-sync with your transactions</h3>
          <p>
            Expenses, money received, transfers, subscription charges and split bills post themselves as they happen. Re-sync checks everything again
            and posts anything missing. It never posts twice, and leaves locked periods alone.
            {s.synced_at && ` Last full sync: ${fmtDate(s.synced_at.slice(0, 10))}.`}
          </p>
        </div>
        {writable && (
          <button className="btn" onClick={run} disabled={busy}>
            <RefreshCw size={16} /> {busy ? 'Syncing…' : 'Re-sync now'}
          </button>
        )}
      </div>
      {result && (
        <div className="stack">
          <p>
            Posted <b>{result.posted}</b>, re-posted <b>{result.reposted}</b>, reversed <b>{result.reversed}</b>, unchanged <b>{result.unchanged}</b>.
          </p>
          {!!result.locked.length && (
            <div className="alert warn">
              <div className="grow">
                In a locked period, left as they are:
                <ul>{result.locked.map((l) => <li key={l}>{l}</li>)}</ul>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
