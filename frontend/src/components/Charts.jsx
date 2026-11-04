import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useId } from 'react'
import { money } from '../api'
import { useAuth } from '../auth'

// Validated categorical palette, stepped separately for each theme, assigned in this fixed order:
// blue, orange, aqua, yellow, magenta. More than five categories fold into a gray "Other".
const THEMES = {
  light: {
    series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'],
    other: '#b9bec8', axis: '#667085', grid: '#eaecf0', cursor: 'rgba(16,24,40,0.04)', surface: '#ffffff',
  },
  dark: {
    series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181'],
    other: '#4b5568', axis: '#94a3b8', grid: '#243044', cursor: 'rgba(255,255,255,0.05)', surface: '#121926',
  },
}
// Budget state, never used for series identity; always shown with an icon and a label
export const STATUS = { OK: '#0ca30c', WARN: '#fab219', OVER: '#d03b3b' }
const short = (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : v)

export function useChartTheme() {
  const { user } = useAuth()
  const t = THEMES[user?.theme === 'dark' ? 'dark' : 'light']
  return { ...t, tick: { fontSize: 12, fill: t.axis } }
}

function MoneyTooltip({ active, payload, label, colors }) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tip">
      <div className="label">{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey}>
          <span className="swatch" style={{ background: colors?.[p.dataKey] || p.color }} />
          {p.name}: <b>{money(p.value)}</b>
        </div>
      ))}
    </div>
  )
}

export function SpendingChart({ data }) {
  const t = useChartTheme()
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="label" tick={t.tick} tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={12} />
        <YAxis tick={t.tick} tickLine={false} axisLine={false} tickFormatter={short} width={48} />
        <Tooltip content={<MoneyTooltip />} cursor={{ fill: t.cursor }} />
        <Bar dataKey="expenses" name="Spent" fill={t.series[0]} radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function CategoryChart({ data }) {
  const t = useChartTheme()
  const height = Math.max(160, data.length * 36 + 24)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke={t.grid} />
        <XAxis type="number" tick={t.tick} tickLine={false} axisLine={false} tickFormatter={short} />
        <YAxis type="category" dataKey="category" tick={t.tick} tickLine={false} axisLine={false} width={100} />
        <Tooltip content={<MoneyTooltip />} cursor={{ fill: t.cursor }} />
        <Bar dataKey="amount" name="Spent" fill={t.series[0]} radius={[0, 4, 4, 0]} barSize={18} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function FundsVsExpensesChart({ data }) {
  const t = useChartTheme()
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={2}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="label" tick={t.tick} tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={12} />
        <YAxis tick={t.tick} tickLine={false} axisLine={false} tickFormatter={short} width={48} />
        <Tooltip content={<MoneyTooltip />} cursor={{ fill: t.cursor }} />
        <Legend iconType="square" wrapperStyle={{ fontSize: 12, color: t.axis }} />
        <Bar dataKey="funds" name="Funds added" fill={t.series[0]} radius={[4, 4, 0, 0]} maxBarSize={22} />
        <Bar dataKey="expenses" name="Expenses" fill={t.series[1]} radius={[4, 4, 0, 0]} maxBarSize={22} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Small area chart of the monthly totals, for a stat card. `color` follows the overall budget state. */
export function Sparkline({ data, dataKey = 'total', color }) {
  const t = useChartTheme()
  const stroke = color || t.series[0]
  return (
    <ResponsiveContainer width="100%" height={76}>
      <AreaChart data={data} margin={{ top: 6, right: 6, left: 6, bottom: 0 }}>
        <defs>
          <linearGradient id={`spark-${stroke.slice(1)}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
            <stop offset="100%" stopColor={stroke} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="label" hide />
        <Tooltip content={<MoneyTooltip />} cursor={{ stroke: t.grid }} />
        <Area
          type="monotone" dataKey={dataKey} name="Spent" stroke={stroke} strokeWidth={2} fill={`url(#spark-${stroke.slice(1)})`}
          dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: t.surface }} isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/** Top five categories in fixed palette order, the rest folded into "Other". Returns rows with their colour. */
export function useCategorySlices(data, key = 'amount') {
  const t = useChartTheme()
  const rows = data.filter((d) => d[key] > 0)
  const top = rows.slice(0, 5).map((d, i) => ({ name: d.category, value: d[key], color: t.series[i] }))
  const rest = rows.slice(5).reduce((s, d) => s + d[key], 0)
  if (rest > 0) top.push({ name: 'Other', value: rest, color: t.other })
  return top
}

export function DonutChart({ slices, total }) {
  const t = useChartTheme()
  return (
    <div className="donut">
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Tooltip content={<MoneyTooltip />} />
          <Pie
            data={slices} dataKey="value" nameKey="name" innerRadius="70%" outerRadius="96%" startAngle={90} endAngle={-270}
            stroke={t.surface} strokeWidth={2} isAnimationActive={false}
          >
            {slices.map((s) => <Cell key={s.name} fill={s.color} />)}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="donut-center">
        <span>This month</span>
        <b>{money(total)}</b>
      </div>
    </div>
  )
}

const SECTION_SERIES = [
  ['expenses', 'Expenses'],
  ['subscriptions', 'Subscriptions'],
  ['splits', 'Split bills'],
]

/** Spending per month, stacked by where it went. Only the sections the user has are shown. */
// Brighter [top, bottom] gradient pairs for the six-month bars, one per section in SECTION_SERIES order:
// violet, pink, emerald, amber, cyan. Each pair stays the same hue, so a section keeps one identity.
const VIBRANT = {
  light: [['#8b5cf6', '#6d28d9'], ['#f472b6', '#db2777'], ['#34d399', '#059669'], ['#fbbf24', '#f59e0b'], ['#22d3ee', '#0891b2']],
  dark: [['#a78bfa', '#7c3aed'], ['#f9a8d4', '#ec4899'], ['#6ee7b7', '#10b981'], ['#fcd34d', '#f59e0b'], ['#67e8f9', '#06b6d4']],
}

export function MonthsChart({ data, sections }) {
  const t = useChartTheme()
  const { user } = useAuth()
  const gid = useId().replace(/:/g, '')
  const palette = VIBRANT[user?.theme === 'dark' ? 'dark' : 'light']
  const shown = SECTION_SERIES.map((s, i) => [...s, palette[i]]).filter(([k]) => sections.includes(k))
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          {shown.map(([key, , [top, bottom]]) => (
            <linearGradient key={key} id={`${gid}-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={top} />
              <stop offset="100%" stopColor={bottom} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="label" tick={t.tick} tickLine={false} axisLine={{ stroke: t.grid }} />
        <YAxis tick={t.tick} tickLine={false} axisLine={false} tickFormatter={short} width={48} />
        <Tooltip content={<MoneyTooltip colors={Object.fromEntries(shown.map(([k, , [, solid]]) => [k, solid]))} />} cursor={{ fill: t.cursor }} />
        {shown.length > 1 && (
          <Legend
            wrapperStyle={{ fontSize: 12, color: t.axis }}
            payload={shown.map(([key, name, [, solid]]) => ({ id: key, value: name, type: 'square', color: solid }))}
          />
        )}
        {shown.map(([key, name], i) => (
          <Bar
            key={key} dataKey={key} name={name} stackId="m" fill={`url(#${gid}-${key})`} maxBarSize={40}
            stroke={t.surface} strokeWidth={1}
            isAnimationActive={false}
            radius={i === shown.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
