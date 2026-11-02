import { useState } from 'react'
import { FileSpreadsheet, FileText } from 'lucide-react'
import { download, errorMessage, todayISO } from '../api'
import { useAuth } from '../auth'
import { Alert, PageHeader } from '../components/ui'

const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const daysAgo = (n) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return iso(d)
}
const PRESETS = {
  'This month': () => ({ start: todayISO().slice(0, 8) + '01', end: todayISO() }),
  'Last 7 days': () => ({ start: daysAgo(6), end: todayISO() }),
  'Last 30 days': () => ({ start: daysAgo(29), end: todayISO() }),
  'This year': () => ({ start: todayISO().slice(0, 4) + '-01-01', end: todayISO() }),
}

export default function Reports() {
  const { params, target } = useAuth()
  const [preset, setPreset] = useState('This month')
  const [range, setRange] = useState(PRESETS['This month']())
  const [granularity, setGranularity] = useState('daily')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const get = async (format) => {
    setBusy(format)
    setError('')
    try {
      await download('/reports/export', { ...params, ...range, format, granularity }, `expenses.${format}`)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy('')
    }
  }

  const pick = (name) => {
    setPreset(name)
    setRange(PRESETS[name]())
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <PageHeader title="Reports" subtitle={`Download a summary and the detailed list of ${target?.full_name || 'your'}’s expenses.`} />
      <div className="card">
        <div className="card-head">
          <h3>Period</h3>
        </div>
        <div className="filters" style={{ marginBottom: 16 }}>
          <div className="segmented">
            {Object.keys(PRESETS).map((name) => (
              <button key={name} className={preset === name ? 'on' : ''} onClick={() => pick(name)}>
                {name}
              </button>
            ))}
          </div>
        </div>
        <div className="filters">
          <label>From</label>
          <input type="date" value={range.start} max={range.end} onChange={(e) => { setPreset(''); setRange({ ...range, start: e.target.value }) }} />
          <label>To</label>
          <input type="date" value={range.end} min={range.start} onChange={(e) => { setPreset(''); setRange({ ...range, end: e.target.value }) }} />
          <label style={{ marginLeft: 12 }}>Totals per</label>
          <select value={granularity} onChange={(e) => setGranularity(e.target.value)}>
            <option value="daily">Day</option>
            <option value="weekly">Week</option>
            <option value="monthly">Month</option>
            <option value="quarterly">Quarter</option>
            <option value="yearly">Year</option>
          </select>
        </div>
      </div>
      {error && <Alert type="error">{error}</Alert>}
      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <div>
              <h3>Excel workbook</h3>
              <p>Summary with charts, totals per period, and a filterable detailed list.</p>
            </div>
            <FileSpreadsheet size={28} color="#079455" />
          </div>
          <button className="btn block" onClick={() => get('xlsx')} disabled={!!busy}>
            {busy === 'xlsx' ? 'Preparing…' : 'Download .xlsx'}
          </button>
        </div>
        <div className="card">
          <div className="card-head">
            <div>
              <h3>PDF report</h3>
              <p>Summary, breakdown by category and item, and every expense, ready to print.</p>
            </div>
            <FileText size={28} color="#d92d20" />
          </div>
          <button className="btn block" onClick={() => get('pdf')} disabled={!!busy}>
            {busy === 'pdf' ? 'Preparing…' : 'Download .pdf'}
          </button>
        </div>
      </div>
    </div>
  )
}
