import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import AddRounded from '@mui/icons-material/AddRounded'
import ArrowForwardRounded from '@mui/icons-material/ArrowForwardRounded'
import CalendarMonthOutlined from '@mui/icons-material/CalendarMonthOutlined'
import ChevronLeftRounded from '@mui/icons-material/ChevronLeftRounded'
import ChevronRightRounded from '@mui/icons-material/ChevronRightRounded'
import AutoAwesomeRounded from '@mui/icons-material/AutoAwesomeRounded'
import CallMadeRounded from '@mui/icons-material/CallMadeRounded'
import CallReceivedRounded from '@mui/icons-material/CallReceivedRounded'
import CheckCircleRounded from '@mui/icons-material/CheckCircleRounded'
import DonutLargeRounded from '@mui/icons-material/DonutLargeRounded'
import ErrorRounded from '@mui/icons-material/ErrorRounded'
import EventRepeatOutlined from '@mui/icons-material/EventRepeatOutlined'
import InfoOutlined from '@mui/icons-material/InfoOutlined'
import InsightsRounded from '@mui/icons-material/InsightsRounded'
import PauseCircleOutlineRounded from '@mui/icons-material/PauseCircleOutlineRounded'
import PaymentsOutlined from '@mui/icons-material/PaymentsOutlined'
import SavingsOutlined from '@mui/icons-material/SavingsOutlined'
import StarRounded from '@mui/icons-material/StarRounded'
import TrendingDownRounded from '@mui/icons-material/TrendingDownRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import TuneRounded from '@mui/icons-material/TuneRounded'
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded'
import api, { errorMessage, money, todayISO } from '../api'
import { canUseSplits, hasFeature, useAuth } from '../auth'
import { accountLabel } from '../components/Accounts'
import { DonutChart, MonthsChart, Sparkline, SpendingChart, STATUS, useCategorySlices } from '../components/Charts'
import { Alert, usePageBanner } from '../components/ui'

const LEVEL = {
  OK: { label: 'On track', Icon: CheckCircleRounded, cls: 'ok' },
  WARN: { label: 'Near limit', Icon: WarningAmberRounded, cls: 'warn' },
  OVER: { label: 'Over budget', Icon: ErrorRounded, cls: 'bad' },
}
const TONE_ICON = { good: TrendingDownRounded, bad: TrendingUpRounded, neutral: InfoOutlined }
const SECTIONS = [
  ['total', 'All spending'],
  ['expenses', 'Expenses'],
  ['subscriptions', 'Subscriptions'],
  ['splits', 'Split bills'],
  ['funds', 'Money received'],
]
const LOGO_COLORS = ['#4f46e5', '#0e9384', '#c4320a', '#7a5af8', '#dd2590', '#1570ef', '#4e5ba6', '#099250']
const logoColor = (name) => LOGO_COLORS[[...name].reduce((s, c) => s + c.charCodeAt(0), 0) % LOGO_COLORS.length]
const fmtDate = (d) => new Date(d + 'T00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
const dueLabel = (days) => (days === 0 ? 'today' : days === 1 ? 'tomorrow' : `on`)
/** "2026-10" for a date or ISO string, and the months from `first` to `last` (newest first) for the picker. */
const ym = (d) => (typeof d === 'string' ? d.slice(0, 7) : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
const monthName = (key, opts = { month: 'long', year: 'numeric' }) => new Date(`${key}-01T00:00`).toLocaleDateString('en-IN', opts)
function monthRange(first, last) {
  const out = []
  const d = new Date(`${last}-01T00:00`)
  const stop = new Date(`${first}-01T00:00`)
  while (d >= stop && out.length < 240) {
    out.push(ym(d))
    d.setMonth(d.getMonth() - 1)
  }
  return out
}
const shiftMonth = (key, n) => {
  const d = new Date(`${key}-01T00:00`)
  d.setMonth(d.getMonth() + n)
  return ym(d)
}

const FREQ = { WEEKLY: 'Weekly', MONTHLY: 'Monthly', QUARTERLY: 'Quarterly', HALF_YEARLY: 'Half-yearly', YEARLY: 'Yearly', CUSTOM: 'Custom' }

/** Change in percent as a coloured chip. For spending, going up is bad; for money received, it's good. */
function Change({ pct, inverse, suffix }) {
  if (pct === null || pct === undefined) return <span className="chip">no data to compare</span>
  const up = pct > 0
  const good = inverse ? up : !up
  const Icon = up ? TrendingUpRounded : TrendingDownRounded
  return (
    <span className={`chip ${pct === 0 ? '' : good ? 'good' : 'bad'}`}>
      <Icon style={{ fontSize: 16 }} />
      {Math.abs(pct).toFixed(1)}%{suffix ? ` ${suffix}` : ''}
    </span>
  )
}

function CardHead({ icon: Icon, title, children }) {
  return (
    <div className="dcard-head">
      <Icon style={{ fontSize: 20 }} />
      <span>{title}</span>
      {children}
    </div>
  )
}

function Progress({ percent, level }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
      <i style={{ width: `${Math.min(100, percent)}%`, background: level === 'OK' ? 'var(--primary)' : STATUS[level] }} />
    </div>
  )
}

function MiniBars({ values }) {
  const max = Math.max(...values, 1)
  return (
    <span className="minibars" aria-hidden="true">
      {values.map((v, i) => (
        <i key={i} className={i === values.length - 1 ? 'now' : ''} style={{ height: `${Math.max(2, (v / max) * 22)}px` }} />
      ))}
    </span>
  )
}

export default function Dashboard() {
  const { params, target, user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [balance, setBalance] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [section, setSection] = useState('total')
  const [error, setError] = useState('')
  const [search, setSearch] = useSearchParams()
  const banner = usePageBanner()
  const thisMonth = ym(new Date())
  // The month being looked at, kept in the address (?month=2026-10) so it survives a refresh and can be shared
  const month = /^\d{4}-\d{2}$/.test(search.get('month') || '') && search.get('month') < thisMonth ? search.get('month') : thisMonth
  const setMonth = (m) => setSearch(m === thisMonth ? {} : { month: m }, { replace: true })

  const load = useCallback(() => {
    const [y, m] = month.split('-').map(Number)
    const end = month === thisMonth ? todayISO() : `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`
    Promise.all([
      api.get('/dashboard/insights', { params: { ...params, ...(month === thisMonth ? {} : { month }) } }),
      api.get('/dashboard', { params: { ...params, start: `${month}-01`, end } }),
      api.get('/accounts', { params }),
    ])
      .then(([i, d, a]) => {
        setData(i.data)
        setBalance(d.data)
        setAccounts(a.data.filter((x) => x.is_active))
        setError('')
      })
      .catch((e) => setError(errorMessage(e)))
  }, [params, month, thisMonth])
  useEffect(load, [load])

  const slices = useCategorySlices(data?.categories || [], 'this_month')

  if (error) return <Alert type="error">{error}</Alert>
  if (!data || !balance) return <div className="muted">Loading…</div>

  const mine = target?.id === user.id
  const writable = target?.writable
  const firstName = balance.user.full_name.split(' ')[0]
  const total = data.sections.total
  const overall = data.budgets.find((b) => b.scope === 'OVERALL')
  const subsOn = hasFeature(user, 'subscription') || data.subscriptions.active > 0
  const splitsOn = canUseSplits(user) || data.months.some((m) => m.splits > 0)
  const shownSections = ['expenses', ...(subsOn ? ['subscriptions'] : []), ...(splitsOn ? ['splits'] : [])]
  const categoryBudget = Object.fromEntries(data.budgets.filter((b) => b.scope === 'CATEGORY').map((b) => [b.target.toLowerCase(), b]))
  const lead = data.insights[0]
  const cmp = data.sections[section]
  const sparkColor = overall && overall.level !== 'OK' ? STATUS[overall.level] : undefined
  const sixMonthAvg = total.avg_6m
  const current = data.is_current
  const label = current ? 'This month' : monthName(month)
  const short = current ? 'This month' : monthName(month, { month: 'short', year: 'numeric' })
  const prevLabel = current ? 'last month' : monthName(shiftMonth(month, -1), { month: 'long' })
  const months = monthRange(data.first_month < thisMonth ? data.first_month.slice(0, 7) : thisMonth, thisMonth)
  const period = balance.period

  const pause = async (sub) => {
    if (!window.confirm(`Pause ${sub.name}? No more reminders or charges will be logged until you resume it.\n\nThis doesn't cancel the payment with your bank or UPI app.`)) return
    try {
      await api.post('/subscriptions/toggle', { ids: [sub.id], is_active: false })
      load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div className="dash">
      <div className="page-head">
        <div>
          <h1>Overview · {current ? monthName(month) : label}</h1>
          <p>
            {mine ? `Welcome back, ${firstName}!` : `Viewing ${balance.user.full_name} (@${balance.user.username})`}{' '}
            {current ? `Day ${data.day_of_month} of ${data.days_in_month}.` : `Looking back at ${label}.`}
          </p>
        </div>
        <div className="filters">
          <div className="month-picker" role="group" aria-label="Month to show">
            <button className="icon-btn" title="Previous month" aria-label="Previous month" disabled={month <= months[months.length - 1]} onClick={() => setMonth(shiftMonth(month, -1))}>
              <ChevronLeftRounded style={{ fontSize: 20 }} />
            </button>
            <label className="month-select">
              <CalendarMonthOutlined style={{ fontSize: 18 }} />
              <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month">
                {months.map((m) => (
                  <option key={m} value={m}>
                    {m === thisMonth ? `This month (${monthName(m, { month: 'short', year: 'numeric' })})` : monthName(m)}
                  </option>
                ))}
              </select>
            </label>
            <button className="icon-btn" title="Next month" aria-label="Next month" disabled={current} onClick={() => setMonth(shiftMonth(month, 1))}>
              <ChevronRightRounded style={{ fontSize: 20 }} />
            </button>
            {!current && (
              <button className="btn ghost small" onClick={() => setMonth(thisMonth)}>
                Back to this month
              </button>
            )}
          </div>
          {writable && (
            <>
              <button className="btn secondary" onClick={() => navigate('/settings#budgets')}>
                <TuneRounded style={{ fontSize: 18 }} /> Manage budget
              </button>
              <Link to="/daily" className="btn">
                <AddRounded style={{ fontSize: 18 }} /> Add expense
              </Link>
            </>
          )}
        </div>
      </div>

      {banner}

      {/* ---------- headline cards ---------- */}
      <div className="dash-top">
        <div className="dcard">
          <CardHead icon={PaymentsOutlined} title={current ? 'Monthly spending' : `Spent in ${label}`} />
          <div className="dcard-body">
            <div className="big-value">{money(total.this_month)}</div>
            <div className="change-line">
              <Change pct={total.vs_last_month} />
              <span className="muted">{current ? 'vs this point last month' : `vs all of ${prevLabel}`}</span>
            </div>
          </div>
          <Sparkline data={data.months} color={sparkColor} />
        </div>

        <div className="dcard">
          <CardHead icon={SavingsOutlined} title="Budget">
            {overall && (
              <span className={`badge ${LEVEL[overall.level].cls}`}>
                {(() => {
                  const I = LEVEL[overall.level].Icon
                  return <I style={{ fontSize: 14 }} />
                })()}
                {LEVEL[overall.level].label}
              </span>
            )}
          </CardHead>
          <div className="dcard-body">
            {overall ? (
              <>
                <div className="big-value">{overall.percent.toFixed(0)}%</div>
                <p className="muted">
                  {current
                    ? `used · ${money(Math.max(0, overall.monthly_limit - overall.spent))} left for ${data.days_in_month - data.day_of_month} more day(s)`
                    : `used in ${label}`}
                </p>
                <div className="grow-space" />
                <Progress percent={overall.percent} level={overall.level} />
                <div className="row-between small muted" style={{ marginTop: 8 }}>
                  <span>Budget: {money(overall.monthly_limit)}</span>
                  <span>
                    <b className="text">{money(overall.spent)}</b>/{money(overall.monthly_limit)}
                  </span>
                </div>
              </>
            ) : (
              <>
                <div className="big-value muted">—</div>
                <p className="muted">No monthly limit yet. Set one and get an alert at 90%.</p>
                <div className="grow-space" />
                {writable && (
                  <Link to="/settings#budgets" className="btn secondary block">
                    Set a budget
                  </Link>
                )}
              </>
            )}
          </div>
        </div>

        {subsOn ? (
          <div className="dcard">
            <CardHead icon={EventRepeatOutlined} title="Active subscriptions" />
            <div className="dcard-body">
              <div className="big-value">{data.subscriptions.active}</div>
              <p className="muted">
                {data.subscriptions.due_7_days} renewal{data.subscriptions.due_7_days === 1 ? '' : 's'} in the next 7 days
              </p>
              <div className="grow-space" />
              <div className="row-between small muted">
                <span>Per month</span>
                <b className="text">{money(data.subscriptions.monthly_total)}</b>
              </div>
              <div className="row-between small muted">
                <span>Per year</span>
                <b className="text">{money(data.subscriptions.monthly_total * 12)}</b>
              </div>
            </div>
          </div>
        ) : (
          <div className="dcard">
            <CardHead icon={AccountBalanceOutlined} title="Available balance" />
            <div className="dcard-body">
              <div className={`big-value ${balance.balance < 0 ? 'neg' : ''}`}>{money(balance.balance)}</div>
              <p className="muted">
                {money(balance.total_funds)} received · {money(balance.total_expenses)} spent
              </p>
            </div>
          </div>
        )}

        <div className="dcard hero-insight">
          <div className="hero-glow" />
          <div className="hero-top">
            <AutoAwesomeRounded style={{ fontSize: 22 }} />
            <span>Smart insights</span>
          </div>
          <h3>{lead ? (lead.tone === 'good' ? 'Nice going' : lead.tone === 'bad' ? 'Heads up' : 'This month') : 'Not enough data yet'}</h3>
          <p>{lead ? lead.text : 'Add a few days of expenses and the app will compare your months for you.'}</p>
          <button className="btn hero-btn" onClick={() => document.getElementById('analysis')?.scrollIntoView({ behavior: 'smooth' })}>
            Begin analysis
          </button>
        </div>
      </div>

      {/* ---------- the chosen month at a glance ---------- */}
      <div className="card" id="month">
        <div className="card-head wrap">
          <div>
            <h3>
              <CalendarMonthOutlined style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
              {label} at a glance
            </h3>
            <p>Every expense you recorded {current ? 'this month so far' : `in ${label}`}, day by day, and where it went.</p>
          </div>
          <Link to={`/expenses?start=${period.start}&end=${period.end}`} className="btn ghost small">
            See the transactions <ArrowForwardRounded style={{ fontSize: 16 }} />
          </Link>
        </div>
        <div className="glance-tiles">
          <div>
            <span>Spent</span>
            <b>{money(period.total)}</b>
            <small>{period.count} transaction{period.count === 1 ? '' : 's'}</small>
          </div>
          <div>
            <span>Received</span>
            <b className="net-pos">{money(data.sections.funds.this_month)}</b>
          </div>
          <div>
            <span>Net</span>
            <b className={data.sections.funds.this_month - period.total < 0 ? 'net-neg' : 'net-pos'}>{money(data.sections.funds.this_month - period.total)}</b>
          </div>
          <div>
            <span>Average a day</span>
            <b>{money(period.average_per_day)}</b>
            <small>over {period.days} day{period.days === 1 ? '' : 's'}</small>
          </div>
        </div>
        {period.count ? (
          <div className="glance-grid">
            <div>
              <h4>Spending each day</h4>
              <SpendingChart data={period.daily.map((d) => ({ label: Number(d.date.slice(8)), expenses: d.amount }))} />
            </div>
            <div>
              <h4>Top payees &amp; items</h4>
              <ul className="legend-list">
                {period.by_item.slice(0, 8).map((it) => (
                  <li key={it.item}>
                    <span className="grow" style={{ wordBreak: 'break-word' }}>
                      {it.item}
                      <span className="muted small"> · {it.count}×</span>
                    </span>
                    <b>{money(it.amount)}</b>
                  </li>
                ))}
              </ul>
              {period.by_item.length > 8 && <p className="hint">and {period.by_item.length - 8} more. Name payees in Settings → Transaction aliases to group them.</p>}
            </div>
          </div>
        ) : (
          <p className="muted">No expenses recorded {current ? 'this month yet' : `in ${label}`}.</p>
        )}
      </div>

      {/* ---------- renewals + categories ---------- */}
      <div className="dash-row wide-left">
        {subsOn ? (
          <div className="card">
            <div className="card-head">
              <div>
                <h3>Upcoming renewals</h3>
                <p>Subscriptions renewing in the next 30 days</p>
              </div>
            </div>
            <ul className="renewals">
              {data.subscriptions.upcoming.map((s) => (
                <li key={s.id}>
                  <span className="logo" style={{ background: logoColor(s.name) }}>
                    {s.name[0].toUpperCase()}
                  </span>
                  <div className="grow">
                    <b>{s.name}</b>
                    <span className="muted">
                      Renews {s.days_until <= 1 ? dueLabel(s.days_until) : `${dueLabel(s.days_until)} ${fmtDate(s.date)}`}
                    </span>
                  </div>
                  <div className="amt">
                    <b>{money(s.amount)}</b>
                    <span className="muted">{FREQ[s.frequency] || s.frequency}</span>
                  </div>
                  {writable && (
                    <button className="icon-btn ghost" title={`Pause ${s.name}`} aria-label={`Pause ${s.name}`} onClick={() => pause(s)}>
                      <PauseCircleOutlineRounded style={{ fontSize: 20 }} />
                    </button>
                  )}
                </li>
              ))}
              {!data.subscriptions.upcoming.length && <li className="muted">Nothing renews in the next 30 days.</li>}
            </ul>
            <Link to="/subscriptions" className="btn secondary block" style={{ marginTop: 12 }}>
              View all subscriptions ({data.subscriptions.active + data.subscriptions.paused})
            </Link>
          </div>
        ) : (
          <AccountsCard accounts={accounts} writable={writable} />
        )}

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Spending by category</h3>
              <p>{label}, including subscriptions and your share of split bills</p>
            </div>
          </div>
          {slices.length ? (
            <>
              <DonutChart slices={slices} total={total.this_month} />
              <ul className="legend-list">
                {slices.map((s) => {
                  const b = categoryBudget[s.name.toLowerCase()]
                  const lvl = b && b.level !== 'OK' ? LEVEL[b.level] : null
                  return (
                    <li key={s.name}>
                      <span className="swatch" style={{ background: s.color }} />
                      <span className="grow">{s.name}</span>
                      {lvl && (
                        <span className={`badge ${lvl.cls}`} title={`${b.percent.toFixed(0)}% of ${money(b.monthly_limit)}`}>
                          <lvl.Icon style={{ fontSize: 14 }} /> {b.percent.toFixed(0)}%
                        </span>
                      )}
                      <b>{money(s.value)}</b>
                    </li>
                  )
                })}
              </ul>
            </>
          ) : (
            <p className="muted">No spending in {current ? 'this month' : label} yet.</p>
          )}
        </div>
      </div>

      {/* ---------- budgets + six months ---------- */}
      <div className="dash-row wide-right">
        <div className="card">
          <div className="card-head">
            <div>
              <h3>Budgets</h3>
              <p>{current ? 'This month’s' : `${label}’s`} use of each limit</p>
            </div>
            {writable && (
              <Link to="/settings#budgets" className="btn ghost small">
                Edit <ArrowForwardRounded style={{ fontSize: 16 }} />
              </Link>
            )}
          </div>
          {data.budgets.length ? (
            <ul className="budget-list">
              {data.budgets.map((b) => {
                const lvl = LEVEL[b.level]
                return (
                  <li key={b.id}>
                    <div className="row-between">
                      <b>{b.label}</b>
                      <span className={`badge ${lvl.cls}`}>
                        <lvl.Icon style={{ fontSize: 14 }} /> {lvl.label}
                      </span>
                    </div>
                    <Progress percent={b.percent} level={b.level} />
                    <div className="row-between small muted">
                      <span>
                        {money(b.spent)} of {money(b.monthly_limit)}
                      </span>
                      <span>
                        {b.percent.toFixed(0)}% · alert at {b.alert_percent}%
                      </span>
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="muted">
              Set a limit for all spending or for a category like Food, and you’ll be notified when you reach 90% of it.
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h3>Last six months</h3>
              <p>
                Where your money went each month · 6-month average {money(sixMonthAvg)}
              </p>
            </div>
          </div>
          <MonthsChart data={data.months} sections={shownSections} />
        </div>
      </div>

      {/* ---------- analysis ---------- */}
      <div className="card" id="analysis">
        <div className="card-head wrap">
          <div>
            <h3>
              <InsightsRounded style={{ fontSize: 20, verticalAlign: '-4px', marginRight: 6 }} />
              Month-on-month analysis
            </h3>
            <p>
              {current
                ? `Day ${data.day_of_month} of ${data.days_in_month}: compared with the same point last month, the whole of last month, and your average over the six months before.`
                : `All of ${label}, compared with all of ${prevLabel} and your average over the six months before.`}
            </p>
          </div>
          <div className="segmented" role="tablist" aria-label="What to compare">
            {SECTIONS.filter(([k]) => k === 'total' || k === 'funds' || shownSections.includes(k)).map(([k, label]) => (
              <button key={k} className={section === k ? 'on' : ''} onClick={() => setSection(k)} role="tab" aria-selected={section === k}>
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="cmp-tiles">
          <div>
            <span>{current ? 'This month so far' : label}</span>
            <b>{money(cmp.this_month)}</b>
            {current && section === 'total' && <small>Heading for about {money(cmp.projected)}</small>}
          </div>
          <div>
            <span>{current ? 'Same day last month' : `All of ${prevLabel}`}</span>
            <b>{money(cmp.last_month_to_date)}</b>
            <Change pct={cmp.vs_last_month} inverse={section === 'funds'} />
          </div>
          {current && (
            <div>
              <span>All of last month</span>
              <b>{money(cmp.last_month)}</b>
            </div>
          )}
          <div>
            <span>6-month average</span>
            <b>{money(cmp.avg_6m)}</b>
            <Change pct={cmp.vs_avg} inverse={section === 'funds'} suffix={!current ? '' : section === 'total' ? '(projected)' : '(to date)'} />
          </div>
        </div>

        {data.insights.length > 0 && (
          <ul className="insight-list">
            {data.insights.map((i) => {
              const Icon = TONE_ICON[i.tone]
              return (
                <li key={i.text} className={i.tone}>
                  <Icon style={{ fontSize: 18 }} />
                  <span>{i.text}</span>
                </li>
              )
            })}
          </ul>
        )}

        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Category</th>
                <th className="num">{short}</th>
                <th className="num">{current ? 'Last month' : 'Month before'}</th>
                <th className="num">6-month avg</th>
                <th className="num">Change</th>
                <th>Last 7 months</th>
              </tr>
            </thead>
            <tbody>
              {data.categories.map((c) => (
                <tr key={c.category}>
                  <td className="strong">{c.category}</td>
                  <td className="num strong">{money(c.this_month)}</td>
                  <td className="num">{money(c.last_month)}</td>
                  <td className="num">{money(c.avg_6m)}</td>
                  <td className="num">
                    {c.change_pct === null ? <span className="chip">new</span> : <Change pct={c.change_pct} />}
                  </td>
                  <td>
                    <MiniBars values={c.trend} />
                  </td>
                </tr>
              ))}
              {!data.categories.length && (
                <tr>
                  <td colSpan={6} className="empty">
                    No spending in {current ? 'this month or last month' : `${label} or the month before`}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---------- accounts + split bills ---------- */}
      <div className="dash-row">
        {subsOn && <AccountsCard accounts={accounts} writable={writable} />}
        {splitsOn && (
          <div className="card">
            <div className="card-head">
              <div>
                <h3>Split bills</h3>
                <p>
                  Across {data.splits.groups} group{data.splits.groups === 1 ? '' : 's'}
                </p>
              </div>
              {mine && (
                <Link to="/splits" className="btn ghost small">
                  Open <ArrowForwardRounded style={{ fontSize: 16 }} />
                </Link>
              )}
            </div>
            <div className="split-sum">
              <div>
                <CallMadeRounded style={{ fontSize: 20 }} />
                <span>You owe</span>
                <b className={data.splits.you_owe ? 'net-neg' : ''}>{money(data.splits.you_owe)}</b>
              </div>
              <div>
                <CallReceivedRounded style={{ fontSize: 20 }} />
                <span>Owed to you</span>
                <b className={data.splits.owed_to_you ? 'net-pos' : ''}>{money(data.splits.owed_to_you)}</b>
              </div>
              <div>
                <DonutLargeRounded style={{ fontSize: 20 }} />
                <span>Your share {current ? 'this month' : `in ${label}`}</span>
                <b>{money(data.sections.splits.this_month)}</b>
              </div>
            </div>
          </div>
        )}
        {!subsOn && !splitsOn && (
          <div className="card">
            <div className="card-head">
              <div>
                <h3>Available balance</h3>
                <p>Everything received minus everything spent</p>
              </div>
            </div>
            <div className={`big-value ${balance.balance < 0 ? 'neg' : ''}`}>{money(balance.balance)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function AccountsCard({ accounts, writable }) {
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3>Accounts</h3>
          <p>Balance of each bank account, card and wallet</p>
        </div>
        {writable && (
          <Link to="/settings#accounts" className="btn ghost small">
            Manage <ArrowForwardRounded style={{ fontSize: 16 }} />
          </Link>
        )}
      </div>
      {accounts.length ? (
        <ul className="acct-list">
          {accounts.map((a) => (
            <li key={a.id}>
              <span className="logo" style={{ background: logoColor(a.name) }}>
                <AccountBalanceOutlined style={{ fontSize: 18 }} />
              </span>
              <div className="grow">
                <b>
                  {accountLabel(a)} {a.is_primary && <StarRounded titleAccess="Primary account" style={{ fontSize: 16, color: STATUS.WARN, verticalAlign: '-3px' }} />}
                </b>
                <span className="muted">
                  {a.bank || 'Account'} · spent {money(a.spent_this_month)} this month
                </span>
              </div>
              <b className={Number(a.balance) < 0 ? 'net-neg' : ''}>{money(a.balance)}</b>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Add your bank accounts to see where money comes in, moves and goes out.</p>
      )}
    </div>
  )
}
