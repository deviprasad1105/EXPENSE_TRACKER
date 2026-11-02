import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AtSign, BellOff, BellRing, Gauge, CalendarX2, CheckCheck, Info, Repeat, Share2, ShieldAlert, SplitSquareHorizontal, UserCheck, Users } from 'lucide-react'
import api from '../api'
import { PageHeader } from '../components/ui'

const KIND = {
  MISSED_ENTRY: { icon: CalendarX2, action: ['/daily', 'Add expenses'] },
  TEAM_MISSED: { icon: Users },
  APPROVAL: { icon: UserCheck, action: ['/admin', 'Review'] },
  USERNAME_REQUEST: { icon: AtSign },
  SHARE: { icon: Share2, action: ['/sharing', 'Open'] },
  SPLIT: { icon: SplitSquareHorizontal, action: ['/splits', 'Open'] },
  SUBSCRIPTION_DUE: { icon: BellRing, action: ['/subscriptions', 'Manage'] },
  SUBSCRIPTION_CHARGED: { icon: Repeat, action: ['/subscriptions', 'Open'] },
  SUBSCRIPTION_ENDED: { icon: Repeat, action: ['/subscriptions', 'Open'] },
  SECURITY: { icon: ShieldAlert, action: ['/settings', 'Security'] },
  BUDGET_ALERT: { icon: Gauge, action: ['/', 'See dashboard'] },
  INFO: { icon: Info },
}

const ago = (d) => {
  const s = (Date.now() - new Date(d).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

export default function Notifications() {
  const [list, setList] = useState([])
  const load = useCallback(async () => setList((await api.get('/notifications')).data), [])
  useEffect(() => {
    load()
  }, [load])

  const read = async (n) => {
    if (n.is_read) return
    await api.post(`/notifications/${n.id}/read`)
    load()
  }
  const readAll = async () => {
    await api.post('/notifications/read-all')
    load()
  }
  const unread = list.filter((n) => !n.is_read).length

  return (
    <div style={{ maxWidth: 900 }}>
      <PageHeader title="Notifications" subtitle={unread ? `${unread} unread` : 'You’re all caught up.'}>
        <button className="btn secondary" onClick={readAll} disabled={!unread}>
          <CheckCheck size={16} /> Mark all as read
        </button>
      </PageHeader>
      <div className="card flush">
        <ul className="list">
          {list.map((n) => {
            const k = KIND[n.kind] || KIND.INFO
            const Icon = k.icon
            return (
              <li key={n.id} className={`notif ${n.is_read ? '' : 'unread'}`} onClick={() => read(n)}>
                <span className="notif-icon">
                  <Icon size={18} />
                </span>
                <div className="notif-body">
                  <div style={{ color: 'var(--text)', fontWeight: n.is_read ? 400 : 600 }}>{n.message}</div>
                  <div className="when">{ago(n.created_at)}</div>
                </div>
                {k.action && (
                  <Link to={k.action[0]} className="btn secondary small">
                    {k.action[1]}
                  </Link>
                )}
                {!n.is_read && <span className="unread-dot" title="Unread" />}
              </li>
            )
          })}
          {!list.length && (
            <li className="empty" style={{ display: 'block' }}>
              <BellOff size={28} />
              No notifications yet.
            </li>
          )}
        </ul>
      </div>
    </div>
  )
}
