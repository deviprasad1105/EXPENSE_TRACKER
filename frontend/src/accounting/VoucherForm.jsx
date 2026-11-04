import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import api, { errorMessage, todayISO } from '../api'
import Modal from '../components/Modal'
import { Msg } from '../components/ui'
import { inr, isCashBank, LedgerOptions, paise, TYPE_HINT, TYPE_LABEL } from './common'

const GST_RATES = ['0', '0.25', '3', '5', '12', '18', '28']
const SALES_GROUPS = new Set(['SALES', 'DIRECT_INCOMES', 'INDIRECT_INCOMES'])
const PURCHASE_GROUPS = new Set(['PURCHASE', 'DIRECT_EXPENSES', 'INDIRECT_EXPENSES', 'FIXED_ASSETS'])
const PARTY_GROUPS = new Set(['SUNDRY_DEBTORS', 'SUNDRY_CREDITORS'])

const blankLine = () => ({ ledger_id: '', debit: '', credit: '' })

/** New voucher: ordinary Dr/Cr lines, or (Sales / Purchase) a GST invoice whose tax is worked out by the server. */
export default function VoucherForm({ books, params, onClose, onSaved }) {
  const { settings, meta, ledgers } = books
  const groups = meta.data?.groups
  const active = (ledgers.data || []).filter((g) => g.is_active)
  const [type, setType] = useState('JOURNAL')
  const [gst, setGst] = useState(false)
  const [date, setDate] = useState(todayISO())
  const [narration, setNarration] = useState('')
  const [lines, setLines] = useState([blankLine(), blankLine()])
  const [inv, setInv] = useState({ invoice_no: '', party_ledger_id: '', place_of_supply: settings.data?.state || '' })
  const [items, setItems] = useState([{ ledger_id: '', taxable_value: '', gst_rate: '18' }])
  const [preview, setPreview] = useState(null)
  const [msg, setMsg] = useState(null)
  const [saving, setSaving] = useState(false)
  const lockDate = settings.data?.lock_date
  const invoiceMode = gst && (type === 'SALES' || type === 'PURCHASE')

  // ---- ordinary voucher ----
  const totals = useMemo(() => {
    const dr = lines.reduce((s, l) => s + paise(l.debit), 0)
    const cr = lines.reduce((s, l) => s + paise(l.credit), 0)
    return { dr, cr }
  }, [lines])
  const setLine = (i, k, v) =>
    setLines((ls) =>
      ls.map((l, j) => {
        if (j !== i) return l
        const next = { ...l, [k]: v }
        // A line is a debit or a credit, not both
        if (k === 'debit' && v) next.credit = ''
        if (k === 'credit' && v) next.debit = ''
        return next
      }),
    )
  const balanceLast = () => {
    const diff = totals.dr - totals.cr
    if (!diff) return
    const amount = (Math.abs(diff) / 100).toFixed(2)
    setLines((ls) => {
      const empty = ls.findIndex((l) => !l.debit && !l.credit)
      const target = empty >= 0 ? empty : ls.length
      const copy = empty >= 0 ? [...ls] : [...ls, blankLine()]
      copy[target] = { ...copy[target], debit: diff < 0 ? amount : '', credit: diff > 0 ? amount : '' }
      return copy
    })
  }
  const complete = lines.filter((l) => l.ledger_id && (paise(l.debit) || paise(l.credit)))
  const balanced = totals.dr > 0 && totals.dr === totals.cr && complete.length >= 2 && complete.length === lines.filter((l) => l.ledger_id || l.debit || l.credit).length

  // ---- GST invoice ----
  const invoiceBody = () => ({
    kind: type,
    invoice_date: date,
    invoice_no: inv.invoice_no,
    party_ledger_id: Number(inv.party_ledger_id),
    place_of_supply: inv.place_of_supply,
    narration: narration || null,
    items: items.filter((i) => i.ledger_id && Number(i.taxable_value) > 0).map((i) => ({ ...i, ledger_id: Number(i.ledger_id) })),
  })
  useEffect(() => {
    if (!invoiceMode) return
    const body = invoiceBody()
    if (!body.items.length || !body.place_of_supply || !body.party_ledger_id) {
      setPreview(null)
      return
    }
    let live = true
    const t = setTimeout(() => {
      api
        .post('/accounting/invoices/preview', { ...body, invoice_no: body.invoice_no || 'preview' }, { params })
        .then((r) => live && setPreview(r.data))
        .catch(() => live && setPreview(null))
    }, 300)
    return () => {
      live = false
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoiceMode, items, inv.place_of_supply, inv.party_ledger_id, type])

  const save = async (e) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setMsg(null)
    try {
      let res
      if (invoiceMode) {
        res = await api.post('/accounting/invoices', invoiceBody(), { params })
      } else {
        res = await api.post(
          '/accounting/vouchers',
          {
            voucher_type: type,
            voucher_date: date,
            narration: narration || null,
            lines: complete.map((l) => ({ ledger_id: Number(l.ledger_id), debit: l.debit || '0', credit: l.credit || '0' })),
          },
          { params },
        )
      }
      onSaved(res.data)
    } catch (err) {
      setMsg({ type: 'error', text: errorMessage(err) })
      setSaving(false)
    }
  }

  const itemFilter = (g) => g.is_active && (type === 'SALES' ? SALES_GROUPS : PURCHASE_GROUPS).has(g.group_code)
  const partyFilter = (g) => g.is_active && (PARTY_GROUPS.has(g.group_code) || isCashBank(g))
  const dateLocked = lockDate && date <= lockDate

  return (
    <Modal title="New voucher" subtitle={TYPE_HINT[type]} onClose={onClose} wide>
      <form onSubmit={save} className="stack" style={{ gap: 16 }}>
        <Msg msg={msg} />
        <div className="segmented" role="tablist">
          {Object.entries(TYPE_LABEL).map(([k, label]) => (
            <button type="button" key={k} className={type === k ? 'on' : ''} onClick={() => setType(k)}>
              {label}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <div>
            <label>Date</label>
            <input type="date" value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} required />
            {dateLocked && <span className="hint" style={{ color: 'var(--danger)' }}>Locked up to {lockDate}: pick a later date</span>}
          </div>
          {(type === 'SALES' || type === 'PURCHASE') && (
            <div>
              <label>&nbsp;</label>
              <label className="check">
                <input type="checkbox" checked={gst} onChange={(e) => setGst(e.target.checked)} /> GST invoice
              </label>
            </div>
          )}
          <div className="span-all">
            <label>Narration</label>
            <input value={narration} maxLength={500} onChange={(e) => setNarration(e.target.value)} placeholder="Being…" />
          </div>
        </div>

        {invoiceMode ? (
          <>
            {!settings.data?.state && (
              <div className="alert warn">Set your business’s state in Accounting settings first: it decides CGST + SGST or IGST.</div>
            )}
            <div className="form-grid">
              <div>
                <label>Invoice no.</label>
                <input value={inv.invoice_no} maxLength={40} onChange={(e) => setInv({ ...inv, invoice_no: e.target.value })} required />
              </div>
              <div>
                <label>{type === 'SALES' ? 'Customer' : 'Supplier'} (or cash / bank)</label>
                <select value={inv.party_ledger_id} onChange={(e) => {
                  const party = active.find((g) => g.id === Number(e.target.value))
                  setInv({ ...inv, party_ledger_id: e.target.value, place_of_supply: party?.state || inv.place_of_supply })
                }} required>
                  <option value="">Choose…</option>
                  <LedgerOptions ledgers={active} groups={groups} filter={partyFilter} />
                </select>
              </div>
              <div>
                <label>Place of supply</label>
                <select value={inv.place_of_supply} onChange={(e) => setInv({ ...inv, place_of_supply: e.target.value })} required>
                  <option value="">Choose…</option>
                  {(meta.data?.states || []).map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{type === 'SALES' ? 'Sales / income ledger' : 'Purchase / expense / asset ledger'}</th>
                    <th className="num">Taxable value</th>
                    <th className="num">GST %</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={i}>
                      <td>
                        <select value={it.ledger_id} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, ledger_id: e.target.value } : x)))}>
                          <option value="">Choose…</option>
                          <LedgerOptions ledgers={active} groups={groups} filter={itemFilter} />
                        </select>
                      </td>
                      <td className="w-amt">
                        <input type="number" min="0.01" step="0.01" value={it.taxable_value}
                          onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, taxable_value: e.target.value } : x)))} />
                      </td>
                      <td className="w-num">
                        <select value={it.gst_rate} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, gst_rate: e.target.value } : x)))}>
                          {GST_RATES.map((r) => (
                            <option key={r} value={r}>{r}%</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {items.length > 1 && (
                          <button type="button" className="btn ghost small" onClick={() => setItems(items.filter((_, j) => j !== i))} title="Remove">
                            <Trash2 size={14} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row-between">
              <button type="button" className="btn secondary small" onClick={() => setItems([...items, { ledger_id: '', taxable_value: '', gst_rate: '18' }])}>
                <Plus size={14} /> Add item
              </button>
              {preview && (
                <div className="gst-preview">
                  <span>Taxable {inr(preview.taxable)}</span>
                  {Object.entries(preview.taxes).map(([k, v]) => (
                    <span key={k}>
                      {type === 'SALES' ? 'Output' : 'Input'} {k} {inr(v)}
                    </span>
                  ))}
                  <b>Total {inr(preview.total)}</b>
                  <span className="badge info">{preview.intra_state ? 'Same state: CGST + SGST' : 'Other state: IGST'}</span>
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Ledger</th>
                    <th className="num">Debit</th>
                    <th className="num">Credit</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i}>
                      <td>
                        <select value={l.ledger_id} onChange={(e) => setLine(i, 'ledger_id', e.target.value)} aria-label={`Line ${i + 1} ledger`}>
                          <option value="">Choose a ledger…</option>
                          <LedgerOptions ledgers={active} groups={groups} />
                        </select>
                      </td>
                      <td className="w-amt">
                        <input type="number" min="0" step="0.01" value={l.debit} onChange={(e) => setLine(i, 'debit', e.target.value)} aria-label={`Line ${i + 1} debit`} />
                      </td>
                      <td className="w-amt">
                        <input type="number" min="0" step="0.01" value={l.credit} onChange={(e) => setLine(i, 'credit', e.target.value)} aria-label={`Line ${i + 1} credit`} />
                      </td>
                      <td>
                        {lines.length > 2 && (
                          <button type="button" className="btn ghost small" onClick={() => setLines(lines.filter((_, j) => j !== i))} title="Remove line">
                            <Trash2 size={14} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="total-row">
                    <td>Total</td>
                    <td className="num">{inr(totals.dr / 100)}</td>
                    <td className="num">{inr(totals.cr / 100)}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
            <div className="row-between">
              <div className="actions">
                <button type="button" className="btn secondary small" onClick={() => setLines([...lines, blankLine()])}>
                  <Plus size={14} /> Add line
                </button>
                {totals.dr !== totals.cr && (
                  <button type="button" className="btn ghost small" onClick={balanceLast}>
                    Balance with {inr(Math.abs(totals.dr - totals.cr) / 100)}
                  </button>
                )}
              </div>
              <span className={`badge ${balanced ? 'ok' : 'warn'}`}>
                {balanced ? 'Balanced' : totals.dr === totals.cr ? 'Add at least two lines' : `Difference ${inr(Math.abs(totals.dr - totals.cr) / 100)}`}
              </span>
            </div>
          </>
        )}

        <div className="form-actions">
          <button type="button" className="btn secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" disabled={saving || dateLocked || (invoiceMode ? !preview : !balanced)}>
            {saving ? 'Posting…' : invoiceMode ? 'Post invoice' : 'Post voucher'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
