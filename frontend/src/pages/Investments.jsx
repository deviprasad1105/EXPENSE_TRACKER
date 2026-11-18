import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AddRounded from '@mui/icons-material/AddRounded'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import SavingsOutlined from '@mui/icons-material/SavingsOutlined'
import ShowChartRounded from '@mui/icons-material/ShowChartRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import api, { errorMessage, money, niceDate } from '../api'
import { useAuth } from '../auth'
import { Msg, PageHeader, StatTile } from '../components/ui'

export const KINDS = {
  MUTUAL_FUND: 'Mutual funds',
  STOCK: 'Shares',
  ETF: 'ETFs',
  CRYPTO: 'Crypto',
  GOLD: 'Gold',
  BOND: 'Bonds',
  DEPOSIT: 'Deposits',
  OTHER: 'Other',
}
const ONE = { MUTUAL_FUND: 'Mutual fund', STOCK: 'Share', ETF: 'ETF', CRYPTO: 'Crypto', GOLD: 'Gold', BOND: 'Bond', DEPOSIT: 'Deposit', OTHER: 'Other' }
const UNIT = { GOLD: 'g', CRYPTO: '', STOCK: 'shares', MUTUAL_FUND: 'units', ETF: 'units' }

const DASH = <span className="muted">—</span>
const amt = (v) => (v == null ? DASH : money(v))
const units = (h) => (h.units == null ? DASH : `${Number(h.units).toLocaleString('en-IN', { maximumFractionDigits: 6 })} ${UNIT[h.kind] ?? ''}`.trim())

function Gain({ gain, pct, block }) {
  if (gain == null) return DASH
  const n = Number(gain)
  const cls = n > 0 ? 'inv-up' : n < 0 ? 'inv-down' : 'muted'
  return (
    <span className={cls} style={block ? { display: 'block' } : undefined}>
      {n > 0 ? '+' : ''}
      {money(n)}
      {pct != null && <span className="inv-pct"> ({n > 0 ? '+' : ''}{pct}%)</span>}
    </span>
  )
}

/** Holdings page; `embedded` when shown as a tab of Financial Reports (no page title of its own). */
export default function Investments({ embedded = false }) {
  const { params, target } = useAuth()
  const canWrite = target?.writable !== false
  const [data, setData] = useState(null)
  const [editing, setEditing] = useState(null) // null, 'new' or a holding
  const [msg, setMsg] = useState(null)

  const load = useCallback(() => api.get('/investments', { params }).then((r) => setData(r.data)), [params])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const run = async (fn, ok) => {
    setMsg(null)
    try {
      await fn()
      await load()
      if (ok) setMsg({ type: 'ok', text: ok })
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }

  const held = (data?.holdings || []).filter((h) => h.is_active)
  const sold = (data?.holdings || []).filter((h) => !h.is_active)
  const value = Number(data?.current_value || 0)
  const diff = data?.accounts_balance != null ? value - Number(data.accounts_balance) : null

  return (
    <div>
      {(() => {
        const add = canWrite && (
          <button className="btn" onClick={() => setEditing('new')}>
            <AddRounded fontSize="small" /> Add investment
          </button>
        )
        return embedded ? (
          <div className="row-between inv-embedded-head">
            <p className="muted" style={{ margin: 0 }}>Your mutual funds, shares, crypto, gold and more, with whatever details you know.</p>
            {add}
          </div>
        ) : (
          <PageHeader title="Investments" subtitle="Your mutual funds, shares, crypto, gold and more, with whatever details you know.">
            {add}
          </PageHeader>
        )
      })()}
      <Msg msg={msg} />

      {editing && (
        <HoldingForm
          h={editing === 'new' ? null : editing}
          run={run}
          params={params}
          close={() => setEditing(null)}
        />
      )}

      {data && (
        <div className="tiles">
          <StatTile hero label="Current value" value={money(value)} sub={`${held.length} holding(s)${data.unvalued ? ` · ${data.unvalued} without a value` : ''}`} icon={ShowChartRounded} />
          <StatTile label="Invested" value={money(data.invested)} sub="Where the amount is known" icon={SavingsOutlined} />
          <StatTile
            label="Gain / loss"
            value={data.gain == null ? '—' : `${Number(data.gain) > 0 ? '+' : ''}${money(data.gain)}`}
            sub={data.gain == null ? 'Needs both invested and current value' : `${data.gain_percent ?? 0}% on holdings with both figures`}
            icon={TrendingUpRounded}
            negative={data.gain != null && Number(data.gain) < 0}
          />
          <StatTile
            label="In your accounts"
            value={data.accounts_balance == null ? '—' : money(data.accounts_balance)}
            sub={
              data.accounts_balance == null
                ? 'No Investment-type account'
                : Math.abs(diff) < 1
                  ? `${data.accounts.join(', ')} · matches`
                  : `${data.accounts.join(', ')} · ${money(Math.abs(diff))} ${diff > 0 ? 'less than' : 'more than'} holdings`
            }
            icon={AccountBalanceOutlined}
          />
        </div>
      )}

      {data && data.groups.length > 0 && (
        <div className="card">
          <h3>Where your money is</h3>
          <div className="inv-alloc">
            {data.groups.map((g) => {
              const share = value ? (Number(g.current_value) / value) * 100 : 0
              return (
                <div key={g.kind} className="inv-alloc-row">
                  <span>{KINDS[g.kind]}</span>
                  <div className="progress" title={`${share.toFixed(1)}%`}>
                    <i style={{ width: `${share}%`, background: 'var(--primary)' }} />
                  </div>
                  <span className="num strong">{money(g.current_value)}</span>
                  <span className="num muted">{share.toFixed(1)}%</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {data && !data.holdings.length && (
        <div className="card">
          <h3>No investments yet</h3>
          <p className="muted">
            Add each mutual fund, share, coin or gold you hold. Only the name is needed; add units, the amount invested and today’s value when you know them.
            The money itself stays in your Investment account in <Link to="/settings#accounts">Settings → Bank accounts</Link>.
          </p>
        </div>
      )}

      {data?.groups.map((g) => (
        <Group key={g.kind} title={KINDS[g.kind]} g={g} rows={held.filter((h) => h.kind === g.kind)} canWrite={canWrite} setEditing={setEditing} run={run} />
      ))}
      {sold.length > 0 && <Group title="Sold" rows={sold} canWrite={canWrite} setEditing={setEditing} run={run} />}
    </div>
  )
}

function Group({ title, g, rows, canWrite, setEditing, run }) {
  const hasUnits = rows.some((h) => h.units != null)
  const sub = (h) => [!g && ONE[h.kind], h.platform, h.symbol, h.valued_on && `value as of ${niceDate(h.valued_on)}`].filter(Boolean).join(' · ')
  const actions = (h) =>
    canWrite && (
      <>
        <button className="btn ghost small" onClick={() => setEditing(h)}>
          Edit
        </button>
        <button className="btn ghost small" onClick={() => window.confirm(`Delete ${h.name}?`) && run(() => api.delete(`/investments/${h.id}`), `${h.name} deleted.`)}>
          Delete
        </button>
      </>
    )
  return (
    <div className="card">
      <div className="row-between inv-group-head">
        <h3 style={{ margin: 0 }}>
          {title} <span className="muted">· {rows.length}</span>
        </h3>
        {g && (
          <div className="inv-group-total">
            <span className="strong">{money(g.current_value)}</span>
            <Gain gain={g.gain} pct={g.gain_percent} />
          </div>
        )}
      </div>
      <div className="table-wrap inv-table-wrap">
        <table className="table inv-table">
          <thead>
            <tr>
              <th>Name</th>
              {hasUnits && <th className="num">Units</th>}
              {hasUnits && <th className="num">Avg cost</th>}
              <th className="num">Invested</th>
              <th className="num">Current value</th>
              <th className="num">Gain / loss</th>
              {canWrite && <th></th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.id}>
                <td>
                  <div className="strong">{h.name}</div>
                  <div className="hint">{sub(h)}</div>
                  {h.note && <div className="hint inv-note">{h.note}</div>}
                </td>
                {hasUnits && <td className="num">{units(h)}</td>}
                {hasUnits && <td className="num">{amt(h.avg_cost)}</td>}
                <td className="num">{amt(h.invested)}</td>
                <td className="num">
                  {amt(h.current_value)}
                  {h.price != null && <div className="hint">{money(h.price)} each</div>}
                </td>
                <td className="num">
                  <Gain gain={h.gain} pct={h.gain_percent} />
                </td>
                {canWrite && <td className="num inv-actions">{actions(h)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Phones: one block per holding instead of the table */}
      <div className="inv-list">
        {rows.map((h) => (
          <div key={h.id} className="inv-item">
            <div className="inv-item-top">
              <div>
                <div className="strong">{h.name}</div>
                <div className="hint">{sub(h)}</div>
              </div>
              <div className="inv-item-value">
                <div className="strong">{amt(h.current_value)}</div>
                <Gain gain={h.gain} pct={h.gain_percent} block />
              </div>
            </div>
            <div className="inv-item-facts">
              <span>
                <span className="hint">Invested </span>
                {amt(h.invested)}
              </span>
              {h.units != null && (
                <span>
                  <span className="hint">Units </span>
                  {units(h)}
                </span>
              )}
              {h.avg_cost != null && (
                <span>
                  <span className="hint">Avg </span>
                  {money(h.avg_cost)}
                </span>
              )}
            </div>
            {h.note && <div className="hint">{h.note}</div>}
            {canWrite && <div className="inv-item-actions">{actions(h)}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

const FIELDS = ['kind', 'name', 'platform', 'symbol', 'units', 'invested', 'current_value', 'valued_on', 'note']

function HoldingForm({ h, run, params, close }) {
  const [f, setF] = useState(() => ({
    ...Object.fromEntries(FIELDS.map((k) => [k, h?.[k] ?? ''])),
    kind: h?.kind || 'MUTUAL_FUND',
    is_active: h ? h.is_active : true,
  }))
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === '' ? null : v]))
    const ok = await run(
      () => (h ? api.put(`/investments/${h.id}`, body) : api.post('/investments', body, { params })),
      `${f.name} ${h ? 'updated' : 'added'}.`,
    )
    if (ok) close()
  }
  return (
    <form className="card" onSubmit={submit}>
      <h3>{h ? `Edit ${h.name}` : 'Add an investment'}</h3>
      <p className="hint">Only the name is needed. Leave anything you don’t know empty; it shows as “—”.</p>
      <div className="form-grid">
        <div>
          <label>Type</label>
          <select value={f.kind} onChange={set('kind')}>
            {Object.entries(ONE).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>Name</label>
          <input value={f.name} onChange={set('name')} maxLength={120} required placeholder="e.g. HSBC Small Cap Fund" />
        </div>
        <div>
          <label>Held at</label>
          <input value={f.platform} onChange={set('platform')} maxLength={60} placeholder="Groww, Nuvama, CoinDCX…" />
        </div>
        <div>
          <label>Symbol</label>
          <input value={f.symbol} onChange={set('symbol')} maxLength={30} placeholder="e.g. ETH, TATAMOTORS" />
        </div>
        <div>
          <label>{f.kind === 'GOLD' ? 'Grams' : 'Units'}</label>
          <input type="number" min="0" step="any" value={f.units} onChange={set('units')} />
        </div>
        <div>
          <label>Amount invested</label>
          <input type="number" min="0" step="0.01" value={f.invested} onChange={set('invested')} />
        </div>
        <div>
          <label>Current value</label>
          <input type="number" min="0" step="0.01" value={f.current_value} onChange={set('current_value')} />
        </div>
        <div>
          <label>Value as of</label>
          <input type="date" value={f.valued_on} onChange={set('valued_on')} />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label>Note</label>
          <input value={f.note} onChange={set('note')} maxLength={300} />
        </div>
        <div>
          <label className="row-between" style={{ justifyContent: 'flex-start' }}>
            <span className="switch">
              <input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
              <span />
            </span>
            Still held (off once sold)
          </label>
        </div>
        <div className="form-actions">
          <button type="button" className="btn ghost" onClick={close}>
            Cancel
          </button>
          <button className="btn">{h ? 'Save' : 'Add'}</button>
        </div>
      </div>
    </form>
  )
}
