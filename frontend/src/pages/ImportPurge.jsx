import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { Alert, Msg, PageHeader } from '../components/ui'
import { ImportTabs } from './Import'
import DateInput from '../components/DateInput'

const day = (v) => new Date(v).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })

/** Purge a date range: every expense, money received and transfer dated in it (typed or imported), of every account or
 * one chosen account (with the transfers to or from it), backed up first. */
export default function ImportPurge() {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState('')
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [typed, setTyped] = useState('')
  const [progress, setProgress] = useState(null)
  const [msg, setMsg] = useState(null)

  const preview = async (keepMsg = false) => {
    if (!start || !end) return setInfo(null)
    if (start > end) {
      setInfo(null)
      return setMsg({ type: 'error', text: 'The From date must be on or before the To date.' })
    }
    setChecking(true)
    if (!keepMsg) setMsg(null)
    try {
      setInfo((await api.get('/imports/purge/preview', { params: { start, end, account_id: accountId || undefined } })).data)
    } catch (err) {
      setInfo(null)
      setMsg({ type: 'error', text: errorMessage(err) })
    } finally {
      setChecking(false)
    }
  }
  useEffect(() => {
    api.get('/accounts').then((r) => setAccounts(r.data)).catch(() => setAccounts([]))
  }, [])
  useEffect(() => {
    setTyped('')
    preview()
  }, [start, end, accountId])

  const which = info?.account ? ` of ${info.account.name}` : ''

  const ready = info && info.records > 0 && !info.blocks.length && typed.trim().toUpperCase() === 'PURGE'

  const run = async () => {
    if (!window.confirm(`Purge ${info.records} record(s)${which} dated ${day(start)} to ${day(end)}?\n\nThey are removed from the app and reversed in your books. A backup is kept that an administrator can restore.`)) return
    setProgress({ done: 0, total: info.records })
    let backupId = null
    let removed = 0
    try {
      for (;;) {
        const res = (await api.post('/imports/purge', { start, end, account_id: accountId ? Number(accountId) : null, backup_id: backupId })).data
        backupId = res.backup_id
        removed += res.removed
        setProgress({ done: removed, total: info.records })
        if (!res.remaining) break
      }
      setMsg({ type: 'ok', text: `${removed} record(s)${which} dated ${day(start)} to ${day(end)} were purged. You can now import that period again.` })
      setTyped('')
      await preview(true)
    } catch (err) {
      setMsg({
        type: 'error',
        text: `${errorMessage(err)}${removed ? ` (${removed} record(s) were removed before this: press Purge again to remove the rest)` : ''}`,
      })
      await preview(true)
    } finally {
      setProgress(null)
    }
  }

  const Line = ({ label, v }) => (
    <tr>
      <td>{label}</td>
      <td className="num">{v.count}</td>
      <td className="num">{money(v.total)}</td>
    </tr>
  )

  return (
    <div>
      <PageHeader title="Purge records" subtitle="Remove every record in a date range, typed in by hand or imported, for example to import that period again from a corrected file.">
        <ImportTabs active="purge" />
      </PageHeader>
      <Msg msg={msg} />
      <div className="card">
        <div className="card-head">
          <div>
            <h3>Date range and account</h3>
            <p>Expenses, money received and transfers dated from the first day to the last day (both included) are removed: of every account, or only the one you choose.</p>
          </div>
        </div>
        <div className="form-grid">
          <div>
            <label htmlFor="purge-from">From</label>
            <DateInput id="purge-from" value={start} max={todayISO()} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div>
            <label htmlFor="purge-to">To</label>
            <DateInput id="purge-to" value={end} max={todayISO()} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <div>
            <label htmlFor="purge-account">Account</label>
            <select id="purge-account" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">All accounts</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}{a.last4 ? ` ••${a.last4}` : ''}{a.is_active === false ? ' (closed)' : ''}
                </option>
              ))}
            </select>
          </div>
        </div>
        {checking && <p className="muted small">Checking…</p>}
        {!start || !end ? <p className="hint">Choose both dates to see what would be removed.</p> : null}
      </div>

      {info && (
        <div className="card">
          <h3>What would be removed</h3>
          {info.records === 0 ? (
            <p className="muted">There are no records{which} from {day(info.start)} to {day(info.end)}.</p>
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Records</th>
                      <th className="num">How many</th>
                      <th className="num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    <Line label="Expenses" v={info.expenses} />
                    <Line label="Money received" v={info.funds} />
                    <Line label={info.account ? `Transfers to or from ${info.account.name}` : 'Transfers between accounts'} v={info.account_transfers} />
                  </tbody>
                </table>
              </div>
              <p className="hint">
                {info.records} record(s){which}: {info.imported} imported and {info.typed} typed in by hand.
                {info.account && info.account_transfers.count > 0 && ` A transfer is removed as a whole, so the other account's side of it goes too.`}
                {info.imports.length > 0 && ` From the imports: ${info.imports.map((b) => `${b.filename} (${b.rows})`).join(', ')}; their rows can be imported again afterwards.`}
                {' '}Split bills and settlements (shared with other people), subscriptions, budgets, accounts and opening balances are not touched.
              </p>
              {info.blocks.map((b) => (
                <Alert key={b} type="error">{b}</Alert>
              ))}
              {!info.blocks.length && (
                <>
                  <Alert type="warn">
                    This can’t be undone from here. Everything listed above is removed from the app and reversed in your accounting books, including
                    records typed in by hand. A backup is kept first, which an administrator can restore from <i>Admin → Backups</i>.
                  </Alert>
                  <div className="form-actions">
                    <div className="grow-space">
                      <label htmlFor="purge-confirm">Type <b>PURGE</b> to confirm</label>
                      <input id="purge-confirm" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} style={{ maxWidth: 220 }} />
                    </div>
                    {progress ? (
                      <div style={{ minWidth: 220 }}>
                        <div className="progress"><i style={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%`, background: 'var(--danger)' }} /></div>
                        <div className="muted small">Purging… {progress.done} of {progress.total}</div>
                      </div>
                    ) : (
                      <button className="btn danger" disabled={!ready} onClick={run}>
                        <Trash2 size={16} /> Purge {info.records} record(s){which}
                      </button>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
      <p className="hint">
        To replace one import with a corrected file instead, use <Link to="/import/history">Purge &amp; re-import</Link> in Import history.
      </p>
    </div>
  )
}
