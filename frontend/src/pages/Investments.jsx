import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AddRounded from '@mui/icons-material/AddRounded'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import SavingsOutlined from '@mui/icons-material/SavingsOutlined'
import ShowChartRounded from '@mui/icons-material/ShowChartRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import api, { errorMessage, money, niceDate } from '../api'
import { useAuth } from '../auth'
import { Alert, Msg, PageHeader, StatTile } from '../components/ui'
import Modal from '../components/Modal'
import { InvestDialog } from '../components/InvestPrompt'
import { useRecordsChanged } from '../components/QuickAdd'
import { KINDS, ONE } from '../investmentKinds'

const UNIT = { GOLD: 'g', CRYPTO: '', STOCK: 'shares', MUTUAL_FUND: 'units', ETF: 'units' }

const DASH = <span className="muted">—</span>
const amt = (v) => (v == null ? DASH : money(v))
const units = (h) => {
  if (h.units == null) return DASH
  const n = Number(h.units)
  const label = UNIT[h.kind] ?? ''
  return `${n.toLocaleString('en-IN', { maximumFractionDigits: 6 })} ${n === 1 && label.endsWith('s') ? label.slice(0, -1) : label}`.trim()
}

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
  // What is folded away (everything is open by default): a holding's id (the lines under it), `t:KIND` (a type in the
  // breakdown), `g:KIND` (a type's section) and `alloc` (the breakdown). Remembered in this browser only.
  const [folded, setFolded] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(FOLD_KEY) || '{}') || {}
    } catch {
      return {}
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(FOLD_KEY, JSON.stringify(folded))
    } catch {
      /* private window: just not remembered */
    }
  }, [folded])
  const [msg, setMsg] = useState(null)

  // Payments that look like buying an investment (a SIP, money sent to a broker), not added yet
  const [pending, setPending] = useState(null)
  const [adding, setAdding] = useState(null)

  const load = useCallback(
    () =>
      Promise.all([api.get('/investments', { params }), api.get('/investments/pending', { params })]).then(([r, p]) => {
        setData(r.data)
        setPending(p.data)
      }),
    [params],
  )
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])
  // Added or skipped from the dialog after saving an expense elsewhere
  useRecordsChanged(useCallback(() => load().catch(() => {}), [load]))

  const run = async (fn, ok, onError) => {
    setMsg(null)
    try {
      await fn()
      await load()
      if (ok) setMsg({ type: 'ok', text: ok })
      return true
    } catch (e) {
      if (onError) onError(errorMessage(e))
      else {
        setMsg({ type: 'error', text: errorMessage(e) })
        window.scrollTo({ top: 0, behavior: 'smooth' })
      }
      return false
    }
  }

  const all = data?.holdings || []
  // Lines that are part of another (e.g. stocks under their broker's total), by parent
  const kids = {}
  for (const h of all) if (h.parent_id != null) (kids[h.parent_id] ||= []).push(h)
  const top = all.filter((h) => h.parent_id == null)
  const held = top.filter((h) => h.is_active)
  const sold = top.filter((h) => !h.is_active)
  const listed = all.length - top.length
  const value = Number(data?.current_value || 0)
  const diff = data?.accounts_balance != null ? value - Number(data.accounts_balance) : null
  const tree = { kids, folded, toggle: (id) => setFolded({ ...folded, [id]: !folded[id] }) }
  const foldAll = () => {
    const all = { alloc: false }
    for (const g of data?.groups || []) all[`t:${g.kind}`] = all[`g:${g.kind}`] = true
    for (const id of Object.keys(kids)) all[id] = true
    all['g:SOLD'] = true
    all.pending = true
    setFolded(all)
  }
  const anyFolded = Object.values(folded).some(Boolean)

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
          parents={top.filter((p) => editing === 'new' || (p.id !== editing.id && !kids[editing.id]))}
          run={run}
          params={params}
          close={() => setEditing(null)}
        />
      )}

      {data && (
        <div className="tiles">
          <StatTile
            hero
            label="Current value"
            value={money(value)}
            sub={`${held.length} holding(s)${listed ? ` · ${listed} listed under them` : ''}${data.unvalued ? ` · ${data.unvalued} without a value` : ''}`}
            icon={ShowChartRounded}
          />
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

      {adding && <InvestDialog item={adding} onDone={() => setAdding(null)} onClose={() => setAdding(null)} />}

      {pending?.items.length > 0 && (
        <div className="card inv-pending">
          <div className={`row-between ${folded.pending ? '' : 'inv-group-head'}`}>
            <h3 style={{ margin: 0 }}>
              <FoldButton open={!folded.pending} onClick={() => tree.toggle('pending')} />
              Waiting to be added <span className="muted">· {pending.items.length}</span>
            </h3>
            <span className="hint">Payments since {niceDate(pending.since)} that look like buying an investment</span>
          </div>
          {!folded.pending &&
            pending.items.map((p) => (
              <div key={`${p.source}-${p.id}`} className="inv-pending-row">
                <div className="inv-pending-what">
                  <div>{p.label}</div>
                  <div className="hint">
                    {niceDate(p.date)}
                    {p.account ? ` · from ${p.account}` : ''}
                    {p.source === 'TRANSFER' ? ' · transfer' : ''}
                  </div>
                </div>
                <span className="strong inv-pending-amount">{money(p.amount)}</span>
                {canWrite && (
                  <>
                    <button className="btn small" onClick={() => setAdding(p)}>
                      Add…
                    </button>
                    <button
                      className="btn ghost small"
                      onClick={() => run(() => api.post(`/investments/pending/${p.source.toLowerCase()}/${p.id}`, { action: 'SKIP' }), 'Marked as not an investment.')}
                    >
                      Not an investment
                    </button>
                  </>
                )}
              </div>
            ))}
        </div>
      )}

      {data && data.groups.length > 0 && (
        <div className="card">
          <div className="row-between inv-group-head">
            <h3 style={{ margin: 0 }}>
              <FoldButton open={!folded.alloc} onClick={() => tree.toggle('alloc')} />
              Where your money is
            </h3>
            <div className="inv-fold-all">
              <button type="button" className="btn ghost small" onClick={() => setFolded({})} disabled={!anyFolded}>
                Expand all
              </button>
              <button type="button" className="btn ghost small" onClick={foldAll}>
                Collapse all
              </button>
            </div>
          </div>
          {/* Each type, then every holding in it (and the lines under a holding), with its share of the total value */}
          {!folded.alloc && (
          <div className="inv-alloc" role="table" aria-label="Breakdown of investments">
            {data.groups.map((g) => {
              const pct = (v) => (value && v != null ? (Number(v) / value) * 100 : null)
              const share = pct(g.current_value)
              const line = (h, child) => {
                const p = pct(h.current_value)
                const n = kids[h.id]?.length
                return (
                  <div key={h.id} className={`inv-alloc-row ${child ? 'inv-alloc-child' : 'inv-alloc-item'}`} role="row">
                    <span className="inv-alloc-name">
                      {n ? <FoldButton open={!folded[h.id]} onClick={() => tree.toggle(h.id)} /> : null}
                      {h.name}
                      {h.platform && !child && <span className="hint"> · {h.platform}</span>}
                      {child && h.kind !== g.kind && <span className="hint"> · {ONE[h.kind]}</span>}
                      {child && h.units != null && <span className="hint"> · {units(h)}</span>}
                    </span>
                    <div className="progress thin" title={p == null ? 'No current value' : `${p.toFixed(1)}%`}>
                      <i style={{ width: `${p || 0}%`, background: 'var(--primary)', opacity: 0.55 }} />
                    </div>
                    <span className="num">
                      {h.current_value != null ? money(h.current_value) : h.invested != null ? <span className="hint">cost {money(h.invested)}</span> : DASH}
                    </span>
                    <span className="num muted">{p == null ? '—' : `${p.toFixed(1)}%`}</span>
                  </div>
                )
              }
              return (
                <div key={g.kind} className="inv-alloc-group" role="rowgroup">
                  <div className="inv-alloc-row inv-alloc-type" role="row">
                    <span className="strong">
                      <FoldButton open={!folded[`t:${g.kind}`]} onClick={() => tree.toggle(`t:${g.kind}`)} />
                      {KINDS[g.kind]} <span className="muted">· {g.count}</span>
                    </span>
                    <div className="progress" title={`${share.toFixed(1)}%`}>
                      <i style={{ width: `${share}%`, background: 'var(--primary)' }} />
                    </div>
                    <span className="num strong">{money(g.current_value)}</span>
                    <span className="num strong">{share.toFixed(1)}%</span>
                  </div>
                  {!folded[`t:${g.kind}`] &&
                    held
                      .filter((h) => h.kind === g.kind)
                      .flatMap((h) => [line(h, false), ...(folded[h.id] ? [] : (kids[h.id] || []).map((c) => line(c, true)))])}
                </div>
              )
            })}
          </div>
          )}
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
        <Group key={g.kind} id={`g:${g.kind}`} title={KINDS[g.kind]} g={g} rows={held.filter((h) => h.kind === g.kind)} tree={tree} canWrite={canWrite} setEditing={setEditing} run={run} />
      ))}
      {sold.length > 0 && <Group id="g:SOLD" title="Sold" rows={sold} tree={tree} canWrite={canWrite} setEditing={setEditing} run={run} />}
    </div>
  )
}

const FOLD_KEY = 'investments-folded'

function FoldButton({ open, onClick }) {
  return (
    <button type="button" className="inv-fold" onClick={onClick} aria-expanded={open} aria-label={open ? 'Collapse' : 'Expand'} title={open ? 'Collapse' : 'Expand'}>
      {open ? '▾' : '▸'}
    </button>
  )
}

function Group({ id, title, g, rows, tree, canWrite, setEditing, run }) {
  const { kids, folded, toggle } = tree
  // Each top-level row, then (unless folded) the lines under it
  const lines = rows.flatMap((h) => [{ h, child: false }, ...(folded[h.id] ? [] : (kids[h.id] || []).map((c) => ({ h: c, child: true })))])
  const hasUnits = lines.some(({ h }) => h.units != null)
  const sub = (h, child) =>
    [(!g || (child && h.kind !== g.kind)) && ONE[h.kind], !child && h.platform, h.symbol, h.valued_on && `value as of ${niceDate(h.valued_on)}`, !h.is_active && 'sold']
      .filter(Boolean)
      .join(' · ')
  const count = (h) => kids[h.id]?.length || 0
  const actions = (h) =>
    canWrite && (
      <>
        <button className="btn ghost small" onClick={() => setEditing(h)}>
          Edit
        </button>
        <button
          className="btn ghost small"
          onClick={() =>
            window.confirm(count(h) ? `Delete ${h.name} and the ${count(h)} holding(s) under it?` : `Delete ${h.name}?`) &&
            run(() => api.delete(`/investments/${h.id}`), `${h.name} deleted.`)
          }
        >
          Delete
        </button>
      </>
    )
  const name = (h, child) => (
    <>
      <div className={child ? '' : 'strong'}>
        {!child && count(h) ? <FoldButton open={!folded[h.id]} onClick={() => toggle(h.id)} /> : null}
        {h.name}
        {!child && count(h) ? (
          <button type="button" className="link-button inv-fold-label" onClick={() => toggle(h.id)}>
            {folded[h.id] ? `show ${count(h)}` : `hide ${count(h)}`}
          </button>
        ) : null}
      </div>
      {sub(h, child) && <div className="hint">{sub(h, child)}</div>}
      {h.note && <div className="hint inv-note">{h.note}</div>}
    </>
  )
  const open = !folded[id]
  return (
    <div className="card">
      <div className={`row-between ${open ? 'inv-group-head' : ''}`}>
        <h3 style={{ margin: 0 }}>
          <FoldButton open={open} onClick={() => toggle(id)} />
          {title} <span className="muted">· {rows.length}</span>
        </h3>
        {g && (
          <div className="inv-group-total">
            <span className="strong">{money(g.current_value)}</span>
            <Gain gain={g.gain} pct={g.gain_percent} />
          </div>
        )}
      </div>
      {open && (
      <>
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
            {lines.map(({ h, child }) => (
              <tr key={h.id} className={child ? 'inv-child' : count(h) ? 'inv-parent' : undefined}>
                <td>{name(h, child)}</td>
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
        {lines.map(({ h, child }) => (
          <div key={h.id} className={`inv-item ${child ? 'inv-child' : ''}`}>
            <div className="inv-item-top">
              <div>{name(h, child)}</div>
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
            {canWrite && <div className="inv-item-actions">{actions(h)}</div>}
          </div>
        ))}
      </div>
      </>
      )}
    </div>
  )
}

const FIELDS = ['kind', 'name', 'platform', 'symbol', 'units', 'invested', 'current_value', 'valued_on', 'note', 'parent_id']

function HoldingForm({ h, parents, run, params, close }) {
  const [f, setF] = useState(() => ({
    ...Object.fromEntries(FIELDS.map((k) => [k, h?.[k] ?? ''])),
    kind: h?.kind || 'MUTUAL_FUND',
    is_active: h ? h.is_active : true,
  }))
  const [err, setErr] = useState(null)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === '' ? null : v]))
    body.parent_id = body.parent_id == null ? null : Number(body.parent_id)
    setErr(null)
    const ok = await run(
      () => (h ? api.put(`/investments/${h.id}`, body) : api.post('/investments', body, { params })),
      `${f.name} ${h ? 'updated' : 'added'}.`,
      setErr,
    )
    if (ok) close()
  }
  // A pop-up, so it opens where you are (a row's Edit is often far down the page)
  return (
    <Modal wide title={h ? `Edit ${h.name}` : 'Add an investment'} subtitle="Only the name is needed. Leave anything you don’t know empty; it shows as “—”." onClose={close}>
    <form onSubmit={submit}>
      {err && <Alert type="error">{err}</Alert>}
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
        <div>
          <label>Part of</label>
          <select value={f.parent_id ?? ''} onChange={set('parent_id')}>
            <option value="">Nothing (counts on its own)</option>
            {parents.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="hint">E.g. a stock under its broker’s total: shown under it and not counted again.</span>
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
    </Modal>
  )
}
