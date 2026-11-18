import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownLeft, ArrowUpRight, Car, Check, Cigarette, Clapperboard, Cookie, HeartPulse, Receipt, ShoppingBag, ShoppingBasket, Tag,
  Utensils,
} from 'lucide-react'
import api, { errorMessage, money, todayISO } from '../api'
import { useAuth } from '../auth'
import { AccountSelect } from './Accounts'
import Modal from './Modal'
import DateInput from './DateInput'

// A big, recognisable icon for the usual categories (anything else gets a tag)
const CATEGORY_ICON = {
  food: Utensils, snacks: Cookie, smoking: Cigarette, transport: Car, groceries: ShoppingBasket, bills: Receipt,
  shopping: ShoppingBag, health: HeartPulse, entertainment: Clapperboard,
}
const iconFor = (c) => CATEGORY_ICON[(c || '').toLowerCase()] || Tag
const SOURCES = ['Salary', 'Refund', 'Gift', 'Interest', 'Other income']

const shiftDay = (iso, days) => {
  const d = new Date(`${iso}T00:00`)
  d.setDate(d.getDate() + days)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

/** Records a change other pages show (they reload when they hear it). */
export const announceChange = () => window.dispatchEvent(new Event('records-changed'))

/** Reload a page's data when a record is added from anywhere (Quick add). */
export function useRecordsChanged(load) {
  useEffect(() => {
    window.addEventListener('records-changed', load)
    return () => window.removeEventListener('records-changed', load)
  }, [load])
}

/** Add one expense or money received in a few taps, from any page. */
export default function QuickAdd({ onClose }) {
  const { params } = useAuth()
  const [kind, setKind] = useState('out')
  const [amount, setAmount] = useState('')
  const [what, setWhat] = useState('')
  const [category, setCategory] = useState('General')
  const [source, setSource] = useState('Salary')
  const [day, setDay] = useState(todayISO())
  const [accountId, setAccountId] = useState(undefined)
  const [categories, setCategories] = useState([])
  const [accounts, setAccounts] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(null)
  const amountRef = useRef(null)

  useEffect(() => {
    Promise.all([api.get('/expenses/categories', { params }), api.get('/accounts', { params })])
      .then(([c, a]) => {
        setCategories(c.data)
        setAccounts(a.data.filter((x) => x.is_active !== false))
      })
      .catch(() => {})
    amountRef.current?.focus()
  }, [params])

  const today = todayISO()
  const yesterday = shiftDay(today, -1)
  const top = [...new Set(['Food', 'Groceries', 'Transport', 'Bills', 'Shopping', 'Health', ...categories])]
    .filter((c) => categories.length === 0 || categories.some((k) => k.toLowerCase() === c.toLowerCase()))
    .slice(0, 9)

  const save = async (e) => {
    e.preventDefault()
    setError('')
    if (!(Number(amount) > 0)) return setError('Enter the amount')
    setBusy(true)
    try {
      let r = null
      if (kind === 'out') {
        r = await api.post('/expenses', {
          expense_date: day, item_name: what.trim() || category, category, quantity: 1, amount: Number(amount), is_extra: true,
          account_id: accountId ?? null,
        }, { params })
      } else {
        await api.post('/funds', { fund_date: day, amount: Number(amount), source: what.trim() || source, account_id: accountId ?? null }, { params })
      }
      announceChange()
      // A SIP / money sent to a broker: ask whether to add it to an investment
      if (r) window.dispatchEvent(new CustomEvent('investment-check', { detail: { expenseIds: [r.data.id] } }))
      setSaved({ kind, amount: Number(amount), label: what.trim() || (kind === 'out' ? category : source) })
      setAmount('')
      setWhat('')
      amountRef.current?.focus()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Quick add" subtitle="Add one expense or money received in a few taps." onClose={onClose}>
      <form className="quick-add" onSubmit={save}>
        <div className="qa-kind" role="tablist" aria-label="Money out or in">
          <button type="button" role="tab" aria-selected={kind === 'out'} className={kind === 'out' ? 'on out' : ''} onClick={() => setKind('out')}>
            <ArrowUpRight size={22} /> Money out
          </button>
          <button type="button" role="tab" aria-selected={kind === 'in'} className={kind === 'in' ? 'on in' : ''} onClick={() => setKind('in')}>
            <ArrowDownLeft size={22} /> Money in
          </button>
        </div>

        <label className="qa-amount">
          <span>₹</span>
          <input ref={amountRef} type="number" inputMode="decimal" min="0" step="0.01" placeholder="0" value={amount}
                 onChange={(e) => setAmount(e.target.value)} aria-label="Amount" />
        </label>

        {kind === 'out' ? (
          <div className="qa-cats" role="radiogroup" aria-label="Category">
            {top.map((c) => {
              const Icon = iconFor(c)
              return (
                <button type="button" key={c} role="radio" aria-checked={category === c} className={category === c ? 'on' : ''} onClick={() => setCategory(c)}>
                  <span><Icon size={24} /></span>
                  {c}
                </button>
              )
            })}
            {categories.length > top.length && (
              <select value={top.includes(category) ? '' : category} onChange={(e) => e.target.value && setCategory(e.target.value)} aria-label="More categories">
                <option value="">More…</option>
                {categories.filter((c) => !top.includes(c)).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
          </div>
        ) : (
          <div className="qa-chips">
            {SOURCES.map((s) => (
              <button type="button" key={s} className={source === s ? 'on' : ''} onClick={() => setSource(s)}>{s}</button>
            ))}
          </div>
        )}

        <div className="field">
          <label>{kind === 'out' ? 'What was it for? (optional)' : 'From whom / what? (optional)'}</label>
          <input value={what} maxLength={100} placeholder={kind === 'out' ? `e.g. Lunch at office (else “${category}”)` : `e.g. November salary (else “${source}”)`}
                 onChange={(e) => setWhat(e.target.value)} />
        </div>

        <div className="field">
          <label>When</label>
          <div className="qa-chips">
            <button type="button" className={day === today ? 'on' : ''} onClick={() => setDay(today)}>Today</button>
            <button type="button" className={day === yesterday ? 'on' : ''} onClick={() => setDay(yesterday)}>Yesterday</button>
            <DateInput value={day} max={today} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Another date" />
          </div>
        </div>

        <AccountSelect accounts={accounts} value={accountId} onChange={setAccountId} label={kind === 'out' ? 'Paid from' : 'Received into'} />

        {error && <div className="alert error">{error}</div>}
        {saved && !error && (
          <div className="alert ok">
            <Check size={18} /> Saved {saved.kind === 'out' ? 'expense' : 'money received'}: {saved.label} · {money(saved.amount)}. Add another, or close.
          </div>
        )}
        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>Close</button>
          <button className="btn" disabled={busy}>
            <Check size={16} /> {busy ? 'Saving…' : kind === 'out' ? 'Save expense' : 'Save money received'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
