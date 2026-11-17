import { createContext, useContext } from 'react'
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

/** A notice the layout shows just below each page's header (e.g. "You haven't added any expenses for today"). */
export const PageBannerContext = createContext(null)
export const usePageBanner = () => useContext(PageBannerContext)
/** The icon of the page being shown (the layout knows it from the menu): shown large beside its title. */
export const PageIconContext = createContext(null)
export function PageIcon() {
  const Icon = useContext(PageIconContext)
  return Icon ? (
    <span className="page-icon" aria-hidden="true">
      <Icon style={{ fontSize: 30 }} />
    </span>
  ) : null
}

export function PageHeader({ title, subtitle, children }) {
  const banner = usePageBanner()
  return (
    <>
      <div className="page-head">
        <div className="page-title">
          <PageIcon />
          <div>
            <h1>{title}</h1>
            {subtitle && <p>{subtitle}</p>}
          </div>
        </div>
        {children && <div className="filters">{children}</div>}
      </div>
      {banner}
    </>
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
