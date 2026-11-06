import { useEffect, useState } from 'react'
import api, { money } from '../api'
import { useAuth } from '../auth'

export const ACCOUNT_KINDS = {
  SAVINGS: 'Savings',
  CURRENT: 'Current',
  SALARY: 'Salary',
  CREDIT_CARD: 'Credit card',
  WALLET: 'Wallet / UPI',
  CASH: 'Cash',
  INVESTMENT: 'Investments (funds, shares)',
  RECEIVABLE: 'Money with others (lent, to get back)',
}

export const accountLabel = (a) => `${a.name}${a.last4 ? ` ••${a.last4}` : ''}`

/** The viewed user's accounts (open ones only unless `all`), reloaded when `version` changes. */
export function useAccounts(version = 0, all = false) {
  const { params } = useAuth()
  const [accounts, setAccounts] = useState([])
  useEffect(() => {
    api
      .get('/accounts', { params })
      .then((r) => setAccounts(all ? r.data : r.data.filter((a) => a.is_active)))
      .catch(() => setAccounts([]))
  }, [params, version, all])
  return accounts
}

/**
 * "Paid from" / "Received in" picker. Starts on the primary account. Hidden when the user has no accounts, or
 * has turned off "Ask which account" in Settings (the server then uses the primary account).
 */
export function AccountSelect({ accounts, value, onChange, label = 'Paid from', showBalance = true }) {
  const { user, target } = useAuth()
  const primary = accounts.find((a) => a.is_primary)
  useEffect(() => {
    if (value === undefined || (value && !accounts.some((a) => a.id === value))) onChange(primary?.id ?? null)
  }, [accounts]) // eslint-disable-line react-hooks/exhaustive-deps
  const asks = target?.id !== user.id || user.ask_account
  if (!accounts.length || !asks) return null
  return (
    <div className="field">
      <label>{label}</label>
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} required>
        {!primary && <option value="">Choose an account…</option>}
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {accountLabel(a)}
            {a.is_primary ? ' (primary)' : ''}
            {showBalance ? ` · ${money(a.balance)}` : ''}
          </option>
        ))}
      </select>
    </div>
  )
}
