import { AlertCircle, CheckCircle2, Info, Inbox, TriangleAlert } from 'lucide-react'

const ALERT_ICON = { ok: CheckCircle2, error: AlertCircle, warn: TriangleAlert, info: Info }

export function Alert({ type = 'info', children }) {
  const Icon = ALERT_ICON[type] || Info
  return (
    <div className={`alert ${type}`} role={type === 'error' ? 'alert' : 'status'}>
      <Icon size={18} />
      <div className="grow">{children}</div>
    </div>
  )
}

/** Shows a {type, text} message object, as kept in page state. */
export const Msg = ({ msg }) => (msg ? <Alert type={msg.type}>{msg.text}</Alert> : null)

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {children && <div className="filters">{children}</div>}
    </div>
  )
}

export function EmptyRow({ cols, children }) {
  return (
    <tr>
      <td colSpan={cols} className="empty">
        <Inbox size={28} />
        {children}
      </td>
    </tr>
  )
}

export function StatTile({ label, value, sub, icon: Icon, hero, negative }) {
  return (
    <div className={`tile ${hero ? 'hero' : ''} ${negative ? 'neg' : ''}`}>
      <div className="tile-top">
        <span className="tile-label">{label}</span>
        {Icon && (
          <span className="tile-icon">
            <Icon size={18} />
          </span>
        )}
      </div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}
