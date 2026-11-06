import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import AddRounded from '@mui/icons-material/AddRounded'
import AutoAwesomeOutlined from '@mui/icons-material/AutoAwesomeOutlined'
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined'
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded'
import EditOutlined from '@mui/icons-material/EditOutlined'
import CategoryOutlined from '@mui/icons-material/CategoryOutlined'
import ContentCopyRounded from '@mui/icons-material/ContentCopyRounded'
import VisibilityOffOutlined from '@mui/icons-material/VisibilityOffOutlined'
import VisibilityOutlined from '@mui/icons-material/VisibilityOutlined'
import ImageOutlined from '@mui/icons-material/ImageOutlined'
import PhonelinkRingOutlined from '@mui/icons-material/PhonelinkRingOutlined'
import SellOutlined from '@mui/icons-material/SellOutlined'
import LightModeOutlined from '@mui/icons-material/LightModeOutlined'
import LogoutOutlined from '@mui/icons-material/LogoutOutlined'
import RestartAltRounded from '@mui/icons-material/RestartAltRounded'
import SavingsOutlined from '@mui/icons-material/SavingsOutlined'
import StarOutlineRounded from '@mui/icons-material/StarOutlineRounded'
import StarRounded from '@mui/icons-material/StarRounded'
import SwapHorizRounded from '@mui/icons-material/SwapHorizRounded'
import api, { errorMessage, money, todayISO } from '../api'
import { hasFeature, useAuth } from '../auth'
import { ACCOUNT_KINDS, accountLabel } from '../components/Accounts'
import AliasModal from '../components/AliasModal'
import { Msg, PageHeader } from '../components/ui'

const EMPTY_ACCOUNT = { name: '', bank: '', kind: 'SAVINGS', last4: '', opening_balance: '', is_primary: false, is_active: true }
const EMPTY_BUDGET = { scope: 'OVERALL', target: '', monthly_limit: '', alert_percent: 90, is_active: true }
const SCOPES = { OVERALL: 'All spending', CATEGORY: 'Category', ITEM: 'Item' }
const LEVEL = { OK: ['ok', 'On track'], WARN: ['warn', 'Near limit'], OVER: ['bad', 'Over budget'] }

export default function Settings() {
  const { user, updateUser, logout } = useAuth()
  const [msg, setMsg] = useState(null)
  const location = useLocation()
  const expense = hasFeature(user, 'expense')

  // Open on the section named in the link, e.g. /settings#budgets from the dashboard
  useEffect(() => {
    if (location.hash) setTimeout(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: 'smooth' }), 150)
  }, [location.hash])

  const save = async (prefs) => updateUser((await api.patch('/profile/preferences', prefs)).data)

  const signOutEverywhere = async () => {
    if (!window.confirm('Sign out on every device and browser, including this one?')) return
    try {
      // The server revokes every token; drop the fresh one it returns so this device signs out too
      await api.post('/auth/logout-all')
      logout()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  return (
    <div style={{ maxWidth: 920 }}>
      <PageHeader title="Settings" subtitle="Personal preferences for this account. They apply on every device you sign in on." />

      <Appearance user={user} save={save} updateUser={updateUser} />
      {expense && <Accounts user={user} save={save} />}
      {expense && <Budgets />}
      {expense && <Categories />}
      {expense && <Aliases />}
      {expense && <UpiSettings user={user} updateUser={updateUser} />}

      <div className="card">
        <h3>Welcome tour</h3>
        <div className="setting-row">
          <div>
            <b>Show the tour when I sign in</b>
            <p>Turned off automatically when you tick “Don’t show again” in the tour.</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={user.show_tour} onChange={(e) => save({ show_tour: e.target.checked })} />
            <span />
          </label>
        </div>
        <div className="setting-row">
          <div>
            <b>Replay the tour</b>
            <p>Walk through the main features again now.</p>
          </div>
          <button className="btn secondary" onClick={() => window.dispatchEvent(new Event('start-tour'))}>
            <AutoAwesomeOutlined style={{ fontSize: 18 }} /> Start tour
          </button>
        </div>
      </div>

      <div className="card">
        <h3>Security</h3>
        <Msg msg={msg} />
        <div className="setting-row">
          <div>
            <b>Sign out of all devices</b>
            <p>Ends every session, for example on a lost phone or a shared computer. You will be signed out here too.</p>
          </div>
          <button className="btn secondary" onClick={signOutEverywhere}>
            <LogoutOutlined style={{ fontSize: 18 }} /> Sign out of all devices
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------- appearance ----------
function Appearance({ user, save, updateUser }) {
  const [msg, setMsg] = useState(null)
  const [preview, setPreview] = useState(null)
  const file = useRef(null)

  useEffect(() => {
    if (!user.background_updated_at) return setPreview(null)
    let url
    api.get('/profile/background', { responseType: 'blob' }).then((r) => setPreview((url = URL.createObjectURL(r.data)))).catch(() => {})
    return () => url && URL.revokeObjectURL(url)
  }, [user.background_updated_at])

  const upload = async (e) => {
    const f = e.target.files[0]
    e.target.value = ''
    if (!f) return
    const form = new FormData()
    form.append('file', f)
    try {
      updateUser((await api.put('/profile/background', form)).data)
      setMsg({ type: 'ok', text: 'Background updated.' })
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const reset = async () => {
    try {
      updateUser((await api.delete('/profile/background')).data)
      setMsg({ type: 'ok', text: 'Back to the default background.' })
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  return (
    <div className="card">
      <h3>Appearance</h3>
      <Msg msg={msg} />
      <div className="setting-row">
        <div>
          <b>Theme</b>
          <p>Choose a light or dark interface.</p>
        </div>
        <div className="segmented">
          <button className={user.theme !== 'dark' ? 'on' : ''} onClick={() => save({ theme: 'light' })}>
            <LightModeOutlined style={{ fontSize: 16 }} /> Light
          </button>
          <button className={user.theme === 'dark' ? 'on' : ''} onClick={() => save({ theme: 'dark' })}>
            <DarkModeOutlined style={{ fontSize: 16 }} /> Dark
          </button>
        </div>
      </div>
      <div className="setting-row">
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <span className="bg-preview" style={preview ? { backgroundImage: `url(${preview})` } : undefined} />
          <div>
            <b>Background picture</b>
            <p>Use your own photo behind the app. JPG, PNG, WEBP or GIF up to 5 MB.</p>
          </div>
        </div>
        <div className="actions">
          <input ref={file} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onChange={upload} />
          <button className="btn secondary" onClick={() => file.current.click()}>
            <ImageOutlined style={{ fontSize: 18 }} /> {preview ? 'Change' : 'Upload'}
          </button>
          {preview && (
            <button className="btn ghost" onClick={reset}>
              <RestartAltRounded style={{ fontSize: 18 }} /> Reset to default
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------- bank accounts & transfers ----------
const TRANSFER_PAGE = 50

function Accounts({ user, save }) {
  const [accounts, setAccounts] = useState([])
  const [transfers, setTransfers] = useState([])
  const [tPage, setTPage] = useState(0) // transfers are shown TRANSFER_PAGE at a time, newest first
  const [tTotal, setTTotal] = useState(0)
  const [form, setForm] = useState(null) // account being added / edited
  const [transfer, setTransfer] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [a, t] = await Promise.all([
      api.get('/accounts'),
      api.get('/accounts/transfers', { params: { offset: tPage * TRANSFER_PAGE, limit: TRANSFER_PAGE } }),
    ])
    setAccounts(a.data)
    setTransfers(t.data)
    setTTotal(Number(t.headers['x-total-count'] || t.data.length))
  }, [tPage])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const run = async (fn, ok) => {
    try {
      await fn()
      if (ok) setMsg({ type: 'ok', text: ok })
      await load()
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }

  const submitAccount = async (e) => {
    e.preventDefault()
    const body = {
      ...form,
      bank: form.bank || null,
      last4: form.last4 || null,
      opening_balance: Number(form.opening_balance || 0),
    }
    const done = await run(
      () => (form.id ? api.put(`/accounts/${form.id}`, body) : api.post('/accounts', body)),
      form.id ? 'Account updated.' : 'Account added.',
    )
    if (done) setForm(null)
  }

  const submitTransfer = async (e) => {
    e.preventDefault()
    const body = { ...transfer, from_account_id: Number(transfer.from_account_id), to_account_id: Number(transfer.to_account_id), amount: Number(transfer.amount), note: transfer.note || null }
    if (await run(() => api.post('/accounts/transfers', body), 'Transfer recorded.')) setTransfer(null)
  }

  const remove = (a) =>
    window.confirm(`Delete ${a.name}? Its expenses and income are kept without an account; transfers to or from it are deleted.`) &&
    run(() => api.delete(`/accounts/${a.id}`), 'Account deleted.')

  const byId = Object.fromEntries(accounts.map((a) => [a.id, a]))
  const open = accounts.filter((a) => a.is_active)
  const setF = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const setT = (k) => (e) => setTransfer({ ...transfer, [k]: e.target.value })

  return (
    <div className="card" id="accounts">
      <div className="card-head">
        <div>
          <h3>
            <AccountBalanceOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
            Bank accounts
          </h3>
          <p>Track which account money comes into, moves between and is spent from. The primary account is used by default.</p>
        </div>
        {!form && (
          <button className="btn secondary small" onClick={() => setForm({ ...EMPTY_ACCOUNT })}>
            <AddRounded style={{ fontSize: 18 }} /> Add account
          </button>
        )}
      </div>
      <Msg msg={msg} />

      {accounts.length > 0 && (
        <div className="table-wrap">
          <table className="table mini-table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Type</th>
                <th className="num">Balance</th>
                <th className="num">Spent this month</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id} className={a.is_active ? '' : 'dim'}>
                  <td>
                    <b>{accountLabel(a)}</b>
                    <div className="hint">
                      {a.bank || '—'}
                      {!a.is_active && ' · closed'}
                    </div>
                  </td>
                  <td>{ACCOUNT_KINDS[a.kind] || a.kind}</td>
                  <td className={`num strong ${Number(a.balance) < 0 ? 'net-neg' : ''}`}>{money(a.balance)}</td>
                  <td className="num">{money(a.spent_this_month)}</td>
                  <td>
                    <div className="actions">
                      {a.is_primary ? (
                        <span className="badge info">
                          <StarRounded style={{ fontSize: 14 }} /> Primary
                        </span>
                      ) : (
                        a.is_active && (
                          <button className="btn ghost small" title="Make primary" onClick={() => run(() => api.post(`/accounts/${a.id}/primary`), `${a.name} is now your primary account.`)}>
                            <StarOutlineRounded style={{ fontSize: 18 }} /> Make primary
                          </button>
                        )
                      )}
                      <button className="btn ghost small" title="Edit" aria-label={`Edit ${a.name}`} onClick={() => setForm({ ...a, bank: a.bank || '', last4: a.last4 || '', opening_balance: a.opening_balance })}>
                        <EditOutlined style={{ fontSize: 18 }} />
                      </button>
                      <button className="btn ghost small" title="Delete" aria-label={`Delete ${a.name}`} style={{ color: 'var(--danger)' }} onClick={() => remove(a)}>
                        <DeleteOutlineRounded style={{ fontSize: 18 }} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!accounts.length && !form && <p className="muted">No accounts yet. Add your salary account, spending account, cards or cash wallet.</p>}

      {form && (
        <form className="inline-form" onSubmit={submitAccount}>
          <div>
            <label>Name</label>
            <input value={form.name} onChange={setF('name')} placeholder="e.g. HDFC Salary" required maxLength={60} />
          </div>
          <div>
            <label>Bank</label>
            <input value={form.bank} onChange={setF('bank')} placeholder="Optional" maxLength={60} />
          </div>
          <div>
            <label>Type</label>
            <select value={form.kind} onChange={setF('kind')}>
              {Object.entries(ACCOUNT_KINDS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Last 4 digits</label>
            <input value={form.last4} onChange={setF('last4')} pattern="\d{4}" maxLength={4} inputMode="numeric" placeholder="Optional" />
          </div>
          <div>
            <label>Balance when added</label>
            <input type="number" step="0.01" value={form.opening_balance} onChange={setF('opening_balance')} placeholder="0" />
          </div>
          <div className="checks" style={{ paddingBottom: 10 }}>
            <label className="check">
              <input type="checkbox" checked={form.is_primary} onChange={setF('is_primary')} /> Primary
            </label>
            {form.id && (
              <label className="check">
                <input type="checkbox" checked={!form.is_active} onChange={(e) => setForm({ ...form, is_active: !e.target.checked })} /> Closed
              </label>
            )}
          </div>
          <div className="form-actions span-all" style={{ gridColumn: '1 / -1' }}>
            <button type="button" className="btn ghost" onClick={() => setForm(null)}>
              Cancel
            </button>
            <button className="btn">{form.id ? 'Save account' : 'Add account'}</button>
          </div>
        </form>
      )}

      <div className="setting-row" style={{ marginTop: 8 }}>
        <div>
          <b>Ask which account for each expense</b>
          <p>
            When on, expense entry asks which account paid (starting on the primary one). When off, every expense goes to the primary account
            automatically.
          </p>
        </div>
        <label className="switch">
          <input type="checkbox" checked={user.ask_account} onChange={(e) => save({ ask_account: e.target.checked })} />
          <span />
        </label>
      </div>

      <div className="card-head" style={{ marginTop: 16 }}>
        <div>
          <h3>Transfers between accounts</h3>
          <p>Money you move between your own accounts, e.g. salary account → spending account. Not counted as income or spending.</p>
        </div>
        {open.length >= 2 && !transfer && (
          <button
            className="btn secondary small"
            onClick={() => setTransfer({ from_account_id: open[0].id, to_account_id: open[1].id, amount: '', transfer_date: todayISO(), note: '' })}
          >
            <SwapHorizRounded style={{ fontSize: 18 }} /> New transfer
          </button>
        )}
      </div>
      {open.length < 2 && <p className="muted">Add at least two accounts to record transfers.</p>}
      {transfer && (
        <form className="inline-form" onSubmit={submitTransfer}>
          <div>
            <label>From</label>
            <select value={transfer.from_account_id} onChange={setT('from_account_id')}>
              {open.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)} · {money(a.balance)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>To</label>
            <select value={transfer.to_account_id} onChange={setT('to_account_id')}>
              {open.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Amount</label>
            <input type="number" min="0.01" step="0.01" value={transfer.amount} onChange={setT('amount')} required />
          </div>
          <div>
            <label>Date</label>
            <input type="date" value={transfer.transfer_date} max={todayISO()} onChange={setT('transfer_date')} required />
          </div>
          <div>
            <label>Note</label>
            <input value={transfer.note} onChange={setT('note')} maxLength={200} placeholder="Optional" />
          </div>
          <div className="form-actions" style={{ gridColumn: '1 / -1' }}>
            <button type="button" className="btn ghost" onClick={() => setTransfer(null)}>
              Cancel
            </button>
            <button className="btn">Record transfer</button>
          </div>
        </form>
      )}
      {transfers.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table mini-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>From → To</th>
                <th className="num">Amount</th>
                <th>Note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {transfers.map((t) => (
                <tr key={t.id}>
                  <td>{t.transfer_date}</td>
                  <td className="strong">
                    {byId[t.from_account_id]?.name || '?'} → {byId[t.to_account_id]?.name || '?'}
                  </td>
                  <td className="num strong">{money(t.amount)}</td>
                  <td className="muted">{t.note}</td>
                  <td>
                    <div className="actions">
                      <button
                        className="btn ghost small"
                        title="Delete"
                        aria-label="Delete transfer"
                        style={{ color: 'var(--danger)' }}
                        onClick={() => window.confirm('Delete this transfer?') && run(() => api.delete(`/accounts/transfers/${t.id}`), 'Transfer deleted.')}
                      >
                        <DeleteOutlineRounded style={{ fontSize: 18 }} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {tTotal > TRANSFER_PAGE && (
            <div className="toolbar" style={{ borderBottom: 0 }}>
              <button type="button" className="btn secondary small" disabled={tPage === 0} onClick={() => setTPage(tPage - 1)}>Newer</button>
              <span className="muted small">
                {tPage * TRANSFER_PAGE + 1}–{Math.min(tTotal, (tPage + 1) * TRANSFER_PAGE)} of {tTotal} transfers
              </span>
              <button type="button" className="btn secondary small" disabled={(tPage + 1) * TRANSFER_PAGE >= tTotal} onClick={() => setTPage(tPage + 1)}>Older</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------- budgets ----------
function Budgets() {
  const [budgets, setBudgets] = useState([])
  const [categories, setCategories] = useState([])
  const [form, setForm] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [b, c] = await Promise.all([api.get('/budgets'), api.get('/expenses/categories')])
    setBudgets(b.data)
    setCategories(c.data)
  }, [])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const submit = async (e) => {
    e.preventDefault()
    const body = { ...form, target: form.scope === 'OVERALL' ? null : form.target, monthly_limit: Number(form.monthly_limit), alert_percent: Number(form.alert_percent) }
    try {
      if (form.id) await api.put(`/budgets/${form.id}`, body)
      else await api.post('/budgets', body)
      setMsg({ type: 'ok', text: 'Budget saved. You’ll get a notification and an email when you reach the alert level.' })
      setForm(null)
      await load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const remove = async (b) => {
    if (!window.confirm(`Delete the budget for ${b.target === '*' ? 'all spending' : b.target}?`)) return
    try {
      await api.delete(`/budgets/${b.id}`)
      await load()
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
    }
  }

  const setF = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const hasOverall = budgets.some((b) => b.scope === 'OVERALL')

  return (
    <div className="card" id="budgets">
      <div className="card-head">
        <div>
          <h3>
            <SavingsOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
            Budgets &amp; alerts
          </h3>
          <p>A monthly limit for all spending, a category (Food) or one item (Cigarettes). You’re alerted at 90% by default and again at 100%.</p>
        </div>
        {!form && (
          <button className="btn secondary small" onClick={() => setForm({ ...EMPTY_BUDGET, scope: hasOverall ? 'CATEGORY' : 'OVERALL' })}>
            <AddRounded style={{ fontSize: 18 }} /> Add budget
          </button>
        )}
      </div>
      <Msg msg={msg} />

      {budgets.length > 0 && (
        <div className="table-wrap">
          <table className="table mini-table">
            <thead>
              <tr>
                <th>For</th>
                <th className="num">Monthly limit</th>
                <th className="num">Spent this month</th>
                <th>Alert at</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {budgets.map((b) => {
                const [cls, label] = b.is_active ? LEVEL[b.level] : ['', 'Off']
                return (
                  <tr key={b.id} className={b.is_active ? '' : 'dim'}>
                    <td>
                      <b>{b.target === '*' ? 'All spending' : b.target}</b>
                      <div className="hint">{b.scope === 'OVERALL' ? 'Every category' : SCOPES[b.scope]}</div>
                    </td>
                    <td className="num strong">{money(b.monthly_limit)}</td>
                    <td className="num">
                      {money(b.spent)} <span className="muted">({b.percent.toFixed(0)}%)</span>
                    </td>
                    <td>{b.alert_percent}%</td>
                    <td>
                      <span className={`badge ${cls}`}>{label}</span>
                    </td>
                    <td>
                      <div className="actions">
                        <button className="btn ghost small" title="Edit" aria-label="Edit budget" onClick={() => setForm({ ...b, target: b.target === '*' ? '' : b.target })}>
                          <EditOutlined style={{ fontSize: 18 }} />
                        </button>
                        <button className="btn ghost small" title="Delete" aria-label="Delete budget" style={{ color: 'var(--danger)' }} onClick={() => remove(b)}>
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
      )}
      {!budgets.length && !form && <p className="muted">No budgets yet.</p>}

      {form && (
        <form className="inline-form" onSubmit={submit}>
          <div>
            <label>Budget for</label>
            <select value={form.scope} onChange={setF('scope')}>
              {Object.entries(SCOPES).map(([k, v]) => (
                <option key={k} value={k} disabled={k === 'OVERALL' && hasOverall && form.scope !== 'OVERALL'}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          {form.scope !== 'OVERALL' && (
            <div>
              <label>{form.scope === 'CATEGORY' ? 'Category' : 'Item name'}</label>
              <input value={form.target} onChange={setF('target')} list="budget-categories" required maxLength={100} />
              {form.scope === 'CATEGORY' && (
                <datalist id="budget-categories">
                  {categories.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              )}
            </div>
          )}
          <div>
            <label>Monthly limit</label>
            <input type="number" min="1" step="0.01" value={form.monthly_limit} onChange={setF('monthly_limit')} required />
          </div>
          <div>
            <label>Alert at {form.alert_percent}%</label>
            <input type="range" min="50" max="100" step="5" value={form.alert_percent} onChange={setF('alert_percent')} style={{ boxShadow: 'none', padding: 0 }} />
          </div>
          <div className="checks" style={{ paddingBottom: 10 }}>
            <label className="check">
              <input type="checkbox" checked={form.is_active} onChange={setF('is_active')} /> Active
            </label>
          </div>
          <div className="form-actions" style={{ gridColumn: '1 / -1' }}>
            <button type="button" className="btn ghost" onClick={() => setForm(null)}>
              Cancel
            </button>
            <button className="btn">{form.id ? 'Save budget' : 'Add budget'}</button>
          </div>
        </form>
      )}
    </div>
  )
}

// ---------- categories ----------
function Categories() {
  const [list, setList] = useState([])
  const [name, setName] = useState('')
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    api.get('/categories').then((r) => setList(r.data)).catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [])

  const run = async (fn, ok) => {
    try {
      setList((await fn()).data)
      setMsg({ type: 'ok', text: ok })
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }
  const add = async (e) => {
    e.preventDefault()
    if (await run(() => api.post('/categories', { name }), `“${name.trim()}” added. It now shows in every category list.`)) setName('')
  }
  const toggle = (c) =>
    run(
      () => api.patch(`/categories/${encodeURIComponent(c.name)}`, { is_hidden: !c.is_hidden }),
      c.is_hidden ? `“${c.name}” shows again.` : `“${c.name}” is hidden from every category list. Its ${c.records} expense(s) keep it.`,
    )
  const remove = (c) =>
    window.confirm(`Remove “${c.name}”?${c.records ? ` Its ${c.records} expense(s) keep it, so it stays in the list while they do.` : ''}`) &&
    run(() => api.delete(`/categories/${encodeURIComponent(c.name)}`), `“${c.name}” removed.`)

  const shown = list.filter((c) => !c.is_hidden)
  const hidden = list.filter((c) => c.is_hidden)

  return (
    <div className="card" id="categories">
      <div className="card-head">
        <div>
          <h3>
            <CategoryOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
            Categories
          </h3>
          <p>Add your own categories, and hide the ones you never use (e.g. Smoking). A hidden category disappears from every list; old expenses keep it.</p>
        </div>
      </div>
      <Msg msg={msg} />
      <form className="inline-form" style={{ marginTop: 0 }} onSubmit={add}>
        <div>
          <label>New category</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Pets, Fuel, Kids" maxLength={50} required />
        </div>
        <div className="form-actions" style={{ alignSelf: 'end' }}>
          <button className="btn secondary">
            <AddRounded style={{ fontSize: 18 }} /> Add category
          </button>
        </div>
      </form>
      <div className="chips-list">
        {shown.map((c) => (
          <span key={c.name} className="cat-chip">
            {c.name}
            {c.records > 0 && <span className="muted small">{c.records}</span>}
            {c.is_custom && <span className="badge info">yours</span>}
            <button className="icon-btn ghost" title={`Hide ${c.name}`} aria-label={`Hide ${c.name}`} onClick={() => toggle(c)}>
              <VisibilityOffOutlined style={{ fontSize: 16 }} />
            </button>
            {c.is_custom && (
              <button className="icon-btn ghost" title={`Remove ${c.name}`} aria-label={`Remove ${c.name}`} style={{ color: 'var(--danger)' }} onClick={() => remove(c)}>
                <DeleteOutlineRounded style={{ fontSize: 16 }} />
              </button>
            )}
          </span>
        ))}
      </div>
      {hidden.length > 0 && (
        <>
          <p className="hint" style={{ margin: '14px 0 6px' }}>Hidden</p>
          <div className="chips-list">
            {hidden.map((c) => (
              <span key={c.name} className="cat-chip off">
                {c.name}
                {c.records > 0 && <span className="muted small">{c.records}</span>}
                <button className="icon-btn ghost" title={`Show ${c.name}`} aria-label={`Show ${c.name}`} onClick={() => toggle(c)}>
                  <VisibilityOutlined style={{ fontSize: 16 }} />
                </button>
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ---------- transaction aliases ----------
export function Aliases() {
  const [aliases, setAliases] = useState([])
  const [groups, setGroups] = useState([])
  const [categories, setCategories] = useState([])
  const [modal, setModal] = useState(null) // { initial } for a new alias, { alias } to rename its old records
  const [editing, setEditing] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const [side, setSide] = useState('all') // all, sent, received
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [a, g, c] = await Promise.all([api.get('/aliases'), api.get('/aliases/groups', { params: { limit: 500 } }), api.get('/expenses/categories')])
    setAliases(a.data)
    setGroups(g.data)
    setCategories(c.data)
  }, [])
  useEffect(() => {
    load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [load])

  const run = async (fn, ok) => {
    try {
      const r = await fn()
      const skipped = r?.data?.skipped_locked ? ` ${r.data.skipped_locked} in a locked accounting period were left as they are.` : ''
      setMsg({ type: 'ok', text: (typeof ok === 'function' ? ok(r) : ok) + skipped })
      await load()
      return true
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return false
    }
  }

  const saveEdit = async (e) => {
    e.preventDefault()
    const body = { alias: editing.alias, category: editing.category || null, clear_category: !editing.category }
    if (await run(() => api.put(`/aliases/${editing.id}`, body), (r) => `Renamed to “${editing.alias}”: ${r.data.renamed} record${r.data.renamed === 1 ? '' : 's'} updated.`))
      setEditing(null)
  }

  const remove = (a) => {
    if (!window.confirm(`Delete the alias “${a.alias}”?`)) return
    const restore = a.records > 0 && window.confirm(`Put the original description back on its ${a.records} record(s)?\n\nOK: show the original descriptions again\nCancel: keep “${a.alias}” as their name`)
    run(() => api.delete(`/aliases/${a.id}`, { params: { restore } }), 'Alias deleted.')
  }

  // Payees without an alias; the ones an alias already matches are its "old records not renamed"
  const unnamed = groups.filter((g) => !g.alias_id)
  const newGroups = unnamed.filter((g) => side === 'all' || (side === 'sent' ? g.expense_count : g.fund_count))
  const shown = showAll ? newGroups : newGroups.slice(0, 8)

  return (
    <div className="card" id="aliases">
      <div className="card-head">
        <div>
          <h3>
            <SellOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
            Transaction aliases
          </h3>
          <p>
            Give a payee a readable name. Every transaction to or from it shows the alias, in history, reports and the UPI tracker, including future
            ones. Example: <i>UPI/DR/4123…/RAMESH TEA STALL/ramesh@ybl</i> → <b>Tea</b>.
          </p>
        </div>
        <button className="btn secondary small" onClick={() => setModal({ initial: {} })}>
          <AddRounded style={{ fontSize: 18 }} /> Add alias
        </button>
      </div>
      <Msg msg={msg} />

      {aliases.length > 0 && (
        <div className="table-wrap">
          <table className="table mini-table">
            <thead>
              <tr>
                <th>Alias</th>
                <th>When a transaction has</th>
                <th>Category</th>
                <th className="num">Records</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {aliases.map((a) =>
                editing?.id === a.id ? (
                  <tr key={a.id}>
                    <td colSpan={5}>
                      <form className="inline-form" style={{ marginTop: 0 }} onSubmit={saveEdit}>
                        <div>
                          <label>Alias</label>
                          <input value={editing.alias} onChange={(e) => setEditing({ ...editing, alias: e.target.value })} required maxLength={100} autoFocus />
                        </div>
                        <div>
                          <label>Category for new ones</label>
                          <input value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })} list="alias-cat-edit" placeholder="Keep their own" maxLength={50} />
                          <datalist id="alias-cat-edit">
                            {categories.map((c) => (
                              <option key={c} value={c} />
                            ))}
                          </datalist>
                        </div>
                        <div className="form-actions" style={{ gridColumn: '1 / -1' }}>
                          <span className="hint" style={{ marginRight: 'auto' }}>All {a.records} record(s) with this alias are renamed too.</span>
                          <button type="button" className="btn ghost" onClick={() => setEditing(null)}>
                            Cancel
                          </button>
                          <button className="btn">Save</button>
                        </div>
                      </form>
                    </td>
                  </tr>
                ) : (
                  <tr key={a.id}>
                    <td className="strong">{a.alias}</td>
                    <td>
                      <code>{a.match_key}</code>
                    </td>
                    <td>{a.category || <span className="muted">—</span>}</td>
                    <td className="num">
                      {a.records}
                      {a.unnamed > 0 && (
                        <div>
                          <button className="btn ghost small" style={{ color: 'var(--warning)' }} onClick={() => setModal({ alias: a })} title="Old records this alias matches but hasn't renamed">
                            +{a.unnamed} not renamed
                          </button>
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        <button className="btn ghost small" title="Rename" aria-label={`Rename ${a.alias}`} onClick={() => setEditing({ id: a.id, alias: a.alias, category: a.category || '' })}>
                          <EditOutlined style={{ fontSize: 18 }} />
                        </button>
                        <button className="btn ghost small" title="Delete" aria-label={`Delete ${a.alias}`} style={{ color: 'var(--danger)' }} onClick={() => remove(a)}>
                          <DeleteOutlineRounded style={{ fontSize: 18 }} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-head wrap" style={{ margin: '18px 0 8px' }}>
        <div>
          <h4 style={{ margin: 0 }}>Payees found in your transactions</h4>
          <p className="hint" style={{ margin: 0 }}>Grouped automatically by UPI ID or description. Set an alias to name them all at once.</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Sent or received">
          {[
            ['all', `All (${unnamed.length})`],
            ['sent', `Sent (${unnamed.filter((g) => g.expense_count).length})`],
            ['received', `Received (${unnamed.filter((g) => g.fund_count).length})`],
          ].map(([k, l]) => (
            <button key={k} className={side === k ? 'on' : ''} onClick={() => setSide(k)}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {newGroups.length === 0 ? (
        <p className="muted">{unnamed.length ? 'No payees of this kind without an alias.' : 'Every payee found has an alias.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="table mini-table">
            <thead>
              <tr>
                <th>Payee</th>
                <th className="num">Paid</th>
                <th className="num">Received</th>
                <th>Last</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((g) => (
                <tr key={g.match_key}>
                  <td style={{ wordBreak: 'break-word' }}>
                    <b>{g.names[0] || g.match_key}</b>
                    <div className="hint">
                      <code>{g.match_key}</code>
                      {g.names.length > 1 && ` · also “${g.names.slice(1).join('”, “')}”`}
                    </div>
                  </td>
                  <td className="num">
                    {g.expense_count ? (
                      <>
                        {money(g.expense_total)}
                        <div className="hint">{g.expense_count}×</div>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="num">
                    {g.fund_count ? (
                      <>
                        <span className="net-pos">{money(g.fund_total)}</span>
                        <div className="hint">{g.fund_count}×</div>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{g.last_date}</td>
                  <td>
                    <button className="btn secondary small" onClick={() => setModal({ initial: { match_key: g.match_key, alias: g.suggestion } })}>
                      Set alias
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {newGroups.length > 8 && (
        <button className="btn ghost small" style={{ marginTop: 8 }} onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Show fewer' : `Show all ${newGroups.length} payees`}
        </button>
      )}

      {modal && (
        <AliasModal
          initial={modal.initial}
          alias={modal.alias}
          categories={categories}
          onClose={() => setModal(null)}
          onSaved={(text) => {
            setModal(null)
            setMsg({ type: 'ok', text })
            load()
          }}
        />
      )}
    </div>
  )
}

// ---------- UPI tracker ----------
function UpiSettings({ user, updateUser }) {
  const allowed = hasFeature(user, 'upi')
  const [data, setData] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [form, setForm] = useState(null)
  const [link, setLink] = useState(null) // shown once, right after it is made
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [s, a] = await Promise.all([api.get('/upi/settings'), api.get('/accounts')])
    setData(s.data)
    setAccounts(a.data.filter((x) => x.is_active))
  }, [])
  useEffect(() => {
    if (allowed) load().catch((e) => setMsg({ type: 'error', text: errorMessage(e) }))
  }, [allowed, load])

  if (!allowed)
    return (
      <div className="card" id="upi">
        <h3>
          <PhonelinkRingOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
          UPI tracker
        </h3>
        <p className="muted">Record your UPI payments automatically from your bank’s alerts. It isn’t part of your plan yet: ask an administrator to add it.</p>
      </div>
    )

  const run = async (fn, ok) => {
    try {
      const r = await fn()
      if (ok) setMsg({ type: 'ok', text: ok })
      await load()
      return r
    } catch (e) {
      setMsg({ type: 'error', text: errorMessage(e) })
      return null
    }
  }

  const toggle = async (enabled) => {
    const r = await run(() => api.patch('/upi/settings', { enabled }), enabled ? 'UPI tracking is on.' : 'UPI tracking is off.')
    if (r) updateUser({ ...user, upi_enabled: enabled })
  }

  const submit = async (e) => {
    e.preventDefault()
    const body = { vpa: form.vpa.trim(), account_id: form.account_id ? Number(form.account_id) : null, is_active: form.is_active }
    const r = await run(() => (form.id ? api.put(`/upi/ids/${form.id}`, body) : api.post('/upi/ids', body)), form.id ? 'UPI ID updated.' : 'UPI ID added.')
    if (r) setForm(null)
  }

  const makeLink = async () => {
    if (data.has_token && !window.confirm('Make a new link? The current one stops working, so update your forwarding app.')) return
    const r = await run(() => api.post('/upi/token'), 'New forwarding link made. Copy it now: it is shown only once.')
    if (r) setLink(`${window.location.origin}${r.data.hook_path}`)
  }

  const copy = (text) => navigator.clipboard?.writeText(text).then(() => setMsg({ type: 'ok', text: 'Copied.' }))
  const byId = Object.fromEntries(accounts.map((a) => [a.id, a]))

  return (
    <div className="card" id="upi">
      <div className="card-head">
        <div>
          <h3>
            <PhonelinkRingOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
            UPI tracker
          </h3>
          <p>
            Your UPI payments are recorded as expenses (and money received as income) from your bank’s UPI alerts, named by your aliases.{' '}
            <Link to="/upi">Open the tracker →</Link>
          </p>
        </div>
      </div>
      <Msg msg={msg} />
      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="setting-row">
            <div>
              <b>Track my UPI payments</b>
              <p>When off, forwarded messages are listed on the tracker but nothing is recorded.</p>
            </div>
            <label className="switch">
              <input type="checkbox" checked={data.enabled} disabled={!data.upi_ids.some((u) => u.is_active)} onChange={(e) => toggle(e.target.checked)} />
              <span />
            </label>
          </div>

          <div className="card-head" style={{ marginTop: 12 }}>
            <div>
              <b>My UPI IDs</b>
              <p className="hint" style={{ margin: 0 }}>Payments are paid from (or received into) the account linked to the UPI ID; the primary account if none.</p>
            </div>
            {!form && (
              <button className="btn secondary small" onClick={() => setForm({ vpa: '', account_id: accounts.find((a) => a.is_primary)?.id || '', is_active: true })}>
                <AddRounded style={{ fontSize: 18 }} /> Add UPI ID
              </button>
            )}
          </div>
          {data.upi_ids.length === 0 && !form && <p className="muted">Add your UPI ID (e.g. yourname@okaxis) to start tracking.</p>}
          {data.upi_ids.length > 0 && (
            <div className="table-wrap">
              <table className="table mini-table">
                <tbody>
                  {data.upi_ids.map((u) => (
                    <tr key={u.id} className={u.is_active ? '' : 'dim'}>
                      <td className="strong">{u.vpa}</td>
                      <td>{u.account_id && byId[u.account_id] ? accountLabel(byId[u.account_id]) : <span className="muted">Primary account</span>}</td>
                      <td>{!u.is_active && <span className="badge">Paused</span>}</td>
                      <td>
                        <div className="actions">
                          <button className="btn ghost small" title="Edit" aria-label={`Edit ${u.vpa}`} onClick={() => setForm({ ...u, account_id: u.account_id || '' })}>
                            <EditOutlined style={{ fontSize: 18 }} />
                          </button>
                          <button
                            className="btn ghost small"
                            title="Delete"
                            aria-label={`Delete ${u.vpa}`}
                            style={{ color: 'var(--danger)' }}
                            onClick={() => window.confirm(`Remove ${u.vpa}? Payments already recorded stay.`) && run(() => api.delete(`/upi/ids/${u.id}`), 'UPI ID removed.')}
                          >
                            <DeleteOutlineRounded style={{ fontSize: 18 }} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {form && (
            <form className="inline-form" onSubmit={submit}>
              <div>
                <label>UPI ID</label>
                <input value={form.vpa} onChange={(e) => setForm({ ...form, vpa: e.target.value })} placeholder="yourname@okaxis" required pattern="[A-Za-z0-9][A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,30}" title="A UPI ID like yourname@okaxis" maxLength={100} />
              </div>
              <div>
                <label>Linked account</label>
                <select value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })}>
                  <option value="">Primary account</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {accountLabel(a)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="checks" style={{ paddingBottom: 10 }}>
                <label className="check">
                  <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active
                </label>
              </div>
              <div className="form-actions" style={{ gridColumn: '1 / -1' }}>
                <button type="button" className="btn ghost" onClick={() => setForm(null)}>
                  Cancel
                </button>
                <button className="btn">{form.id ? 'Save' : 'Add UPI ID'}</button>
              </div>
            </form>
          )}

          <div className="setting-row" style={{ marginTop: 12 }}>
            <div>
              <b>Automatic forwarding link</b>
              <p>
                Set an SMS-forwarding app (Android) or a Shortcuts automation (iPhone) to send your bank’s UPI alerts to this private link, as plain text or
                JSON <code>{'{"text": "…"}'}</code>. Anyone with the link can add records to your account: keep it private.
                {data.has_token && !link && <> Current link ends in <code>…{data.token_hint}</code>.</>}
              </p>
            </div>
            <div className="actions">
              <button className="btn secondary" onClick={makeLink}>
                {data.has_token ? 'New link' : 'Make link'}
              </button>
              {data.has_token && (
                <button
                  className="btn ghost"
                  style={{ color: 'var(--danger)' }}
                  onClick={() => window.confirm('Turn off the forwarding link? Forwarded messages will be refused.') && run(() => api.delete('/upi/token'), 'Link turned off.').then(() => setLink(null))}
                >
                  Turn off
                </button>
              )}
            </div>
          </div>
          {link && (
            <div className="alert info" style={{ display: 'flex', gap: 8, alignItems: 'center', wordBreak: 'break-all' }}>
              <code style={{ flex: 1 }}>{link}</code>
              <button className="btn ghost small" onClick={() => copy(link)} title="Copy">
                <ContentCopyRounded style={{ fontSize: 18 }} /> Copy
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
