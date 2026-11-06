import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import CallMadeRounded from '@mui/icons-material/CallMadeRounded'
import CallReceivedRounded from '@mui/icons-material/CallReceivedRounded'
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded'
import ReceiptLongOutlined from '@mui/icons-material/ReceiptLongOutlined'
import api, { errorMessage, money, todayISO } from '../api'
import AliasModal from '../components/AliasModal'
import { Alert, Msg, PageHeader, StatTile } from '../components/ui'

const STATUS = {
  CREATED: ['ok', 'Recorded'],
  DUPLICATE: ['', 'Already recorded'],
  IGNORED: ['warn', 'Not recorded'],
  FAILED: ['bad', 'Couldn’t read'],
}
const RANGES = [
  [7, '7 days'],
  [30, '30 days'],
  [90, '90 days'],
]

export default function UpiTracker() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState(null)
  const [settings, setSettings] = useState(null)
  const [text, setText] = useState('')
  const [modal, setModal] = useState(null)
  const [filter, setFilter] = useState('all')
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [a, s] = await Promise.all([api.get('/upi/activity', { params: { days } }), api.get('/upi/settings')])
    setData(a.data)
    setSettings(s.data)
  }, [days])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])
  // Forwarded messages arrive in the background: refresh every minute while the page is open
  useEffect(() => {
    const id = setInterval(() => load().catch(() => {}), 60_000)
    return () => clearInterval(id)
  }, [load])

  const paste = async (e) => {
    e.preventDefault()
    try {
      const r = await api.post('/upi/messages', { text })
      const ev = r.data
      const [, label] = STATUS[ev.status]
      setMsg({
        type: ev.status === 'CREATED' ? 'ok' : ev.status === 'FAILED' ? 'error' : 'warn',
        text: ev.status === 'CREATED' ? `${label}: ${ev.direction === 'DEBIT' ? 'paid' : 'received'} ${money(ev.amount)} — ${ev.name}.` : `${label}: ${ev.reason}`,
      })
      if (ev.status !== 'FAILED') setText('')
      await load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const removeEvent = async (ev) => {
    if (!window.confirm('Remove this message from the list? A recorded expense or income stays: delete it on its own page.')) return
    try {
      await api.delete(`/upi/events/${ev.id}`)
      await load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const today = todayISO()
  const todayRow = data?.days.find((d) => d.date === today)
  const totals = useMemo(
    () => (data?.days || []).reduce((t, d) => ({ paid: t.paid + Number(d.paid), received: t.received + Number(d.received), count: t.count + d.count }), { paid: 0, received: 0, count: 0 }),
    [data],
  )
  const events = (data?.events || []).filter((ev) => filter === 'all' || (filter === 'noalias' ? ev.status === 'CREATED' && !ev.alias_id && (ev.expense_id || ev.fund_id) : ev.status !== 'CREATED'))
  const noAlias = (data?.events || []).filter((ev) => ev.status === 'CREATED' && !ev.alias_id && (ev.expense_id || ev.fund_id)).length

  return (
    <div>
      <PageHeader title="UPI Tracker" subtitle="Your UPI payments, recorded from your bank’s alerts and named by your aliases.">
        <div className="segmented">
          {RANGES.map(([d, label]) => (
            <button key={d} className={days === d ? 'on' : ''} onClick={() => setDays(d)}>
              {label}
            </button>
          ))}
        </div>
      </PageHeader>
      <Msg msg={msg} />

      {settings && !settings.enabled && (
        <Alert type="warn">
          UPI tracking is off{settings.upi_ids.length ? '' : ' and no UPI ID is added'}. Turn it on in <Link to="/settings#upi">Settings → UPI tracker</Link>.
        </Alert>
      )}
      {settings?.enabled && !settings.has_token && (
        <Alert type="info">
          To record payments automatically, make your forwarding link in <Link to="/settings#upi">Settings → UPI tracker</Link>. You can also paste a message
          below.
        </Alert>
      )}

      <div className="tiles">
        <StatTile hero label="Paid today" value={money(todayRow?.paid)} sub={`${todayRow?.count || 0} UPI transaction(s) today`} icon={CallMadeRounded} />
        <StatTile label={`Paid · last ${days} days`} value={money(totals.paid)} sub={`${totals.count} transaction(s)`} icon={CallMadeRounded} />
        <StatTile label={`Received · last ${days} days`} value={money(totals.received)} icon={CallReceivedRounded} />
        <StatTile label="Payees without an alias" value={noAlias} sub={noAlias ? 'Name them to make history readable' : 'All named'} icon={ReceiptLongOutlined} />
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Add a message</h3>
          <p className="hint">Paste a UPI alert from your bank or UPI app. It is recorded exactly as a forwarded one would be.</p>
          <form onSubmit={paste}>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              maxLength={2000}
              required
              placeholder="e.g. Rs.250.00 debited from a/c **1234 on 05-11-26 to VPA ramesh@ybl (UPI Ref No 412345678901)."
              style={{ width: '100%', resize: 'vertical' }}
            />
            <div className="form-actions">
              <button className="btn" disabled={!text.trim()}>
                Record
              </button>
            </div>
          </form>
        </div>
        <div className="card">
          <h3>Daily UPI spending</h3>
          {!data?.days.length ? (
            <p className="muted">Nothing recorded in the last {days} days.</p>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 260, overflowY: 'auto' }}>
              <table className="table mini-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className="num">Paid</th>
                    <th className="num">Received</th>
                    <th className="num">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {data.days.map((d) => (
                    <tr key={d.date}>
                      <td>{d.date === today ? 'Today' : d.date}</td>
                      <td className="num strong">{money(d.paid)}</td>
                      <td className="num net-pos">{Number(d.received) ? money(d.received) : '—'}</td>
                      <td className="num">{d.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <h3>Messages received</h3>
            <p>
              Recorded payments are in <Link to="/expenses">Expenses</Link> and <Link to="/funds">Balance</Link>, marked UPI.
            </p>
          </div>
          <div className="segmented">
            {[
              ['all', 'All'],
              ['noalias', `No alias${noAlias ? ` (${noAlias})` : ''}`],
              ['problems', 'Not recorded'],
            ].map(([k, label]) => (
              <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Payee</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {!events.length && (
                <tr>
                  <td colSpan={5} className="muted">
                    {data ? 'No messages.' : 'Loading…'}
                  </td>
                </tr>
              )}
              {events.map((ev) => {
                const [cls, label] = STATUS[ev.status] || ['', ev.status]
                const linked = ev.expense_id || ev.fund_id
                return (
                  <tr key={ev.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {ev.txn_date || ev.received_at.slice(0, 10)}
                      <div className="hint">received {ev.received_at.slice(11, 16)}</div>
                    </td>
                    <td style={{ wordBreak: 'break-word' }} title={ev.raw_text || ''}>
                      <b>{ev.name || ev.counterparty || '—'}</b>
                      {ev.name && ev.counterparty && ev.name !== ev.counterparty && <div className="hint">{ev.counterparty}</div>}
                      {ev.payee_key && <div className="hint"><code>{ev.payee_key}</code>{ev.ref && ` · ref ${ev.ref}`}</div>}
                    </td>
                    <td className={`num strong ${ev.direction === 'CREDIT' ? 'net-pos' : ''}`}>
                      {ev.amount != null ? `${ev.direction === 'CREDIT' ? '+' : '−'}${money(ev.amount)}` : '—'}
                    </td>
                    <td>
                      <span className={`badge ${cls}`}>{label}</span>
                      {ev.status === 'CREATED' && !linked && <div className="hint">Deleted since</div>}
                      {ev.status !== 'CREATED' && ev.reason && <div className="hint">{ev.reason}</div>}
                    </td>
                    <td>
                      <div className="actions">
                        {ev.status === 'CREATED' && linked && !ev.alias_id && ev.payee_key && (
                          <button
                            className="btn secondary small"
                            onClick={() => setModal({ match_key: ev.payee_key, alias: ev.counterparty && !ev.counterparty.includes('@') ? titleCase(ev.counterparty) : '' })}
                          >
                            Set alias
                          </button>
                        )}
                        <button className="btn ghost small" title="Remove from list" aria-label="Remove message" style={{ color: 'var(--danger)' }} onClick={() => removeEvent(ev)}>
                          <DeleteOutlineRounded style={{ fontSize: 18 }} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {modal && (
        <AliasModal
          initial={modal}
          onClose={() => setModal(null)}
          onSaved={(t) => {
            setModal(null)
            setMsg({ type: 'ok', text: t })
            load()
          }}
        />
      )}
    </div>
  )
}

const titleCase = (s) => s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
