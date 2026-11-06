import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import AccountBalanceWalletOutlined from '@mui/icons-material/AccountBalanceWalletOutlined'
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined'
import AdminPanelSettingsOutlined from '@mui/icons-material/AdminPanelSettingsOutlined'
import AssessmentOutlined from '@mui/icons-material/AssessmentOutlined'
import AutoAwesomeOutlined from '@mui/icons-material/AutoAwesomeOutlined'
import BusinessOutlined from '@mui/icons-material/BusinessOutlined'
import CallSplitOutlined from '@mui/icons-material/CallSplitOutlined'
import ChecklistOutlined from '@mui/icons-material/ChecklistOutlined'
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined'
import EditCalendarOutlined from '@mui/icons-material/EditCalendarOutlined'
import EventRepeatOutlined from '@mui/icons-material/EventRepeatOutlined'
import GroupsOutlined from '@mui/icons-material/GroupsOutlined'
import LightModeOutlined from '@mui/icons-material/LightModeOutlined'
import LogoutOutlined from '@mui/icons-material/LogoutOutlined'
import MenuBookOutlined from '@mui/icons-material/MenuBookOutlined'
import NotificationsNoneOutlined from '@mui/icons-material/NotificationsNoneOutlined'
import PersonOutlineOutlined from '@mui/icons-material/PersonOutlineOutlined'
import HomeOutlined from '@mui/icons-material/HomeOutlined'
import ChevronRightRounded from '@mui/icons-material/ChevronRightRounded'
import PhonelinkRingOutlined from '@mui/icons-material/PhonelinkRingOutlined'
import ReceiptLongOutlined from '@mui/icons-material/ReceiptLongOutlined'
import SettingsOutlined from '@mui/icons-material/SettingsOutlined'
import SpaceDashboardOutlined from '@mui/icons-material/SpaceDashboardOutlined'
import SummarizeOutlined from '@mui/icons-material/SummarizeOutlined'
import TuneOutlined from '@mui/icons-material/TuneOutlined'
import UploadFileOutlined from '@mui/icons-material/UploadFileOutlined'
import VisibilityOutlined from '@mui/icons-material/VisibilityOutlined'
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded'
import api, { todayISO } from '../api'
import { canUseSplits, hasFeature, useAuth } from '../auth'
import Avatar from './Avatar'
import Tour from './Tour'
import Logo from './Logo'
import { PageBannerContext } from './ui'

const ACCESS = {
  OWNER: { label: 'Your data', cls: 'ok' },
  ADMIN: { label: 'Admin · full access', cls: 'info' },
  MANAGER: { label: 'Your team · view only', cls: 'warn' },
  SHARED: { label: 'Shared · view only', cls: 'warn' },
  ORG: { label: 'Your organisation · view only', cls: 'warn' },
}
const ROLE_LABEL = { ADMIN: 'Administrator', ORG_ADMIN: 'Organisation admin', USER: 'User' }
const REMIND_AFTER_HOUR = 18

export default function Layout() {
  const { user, logout, accessible, target, setViewUserId, updateUser } = useAuth()
  const [unread, setUnread] = useState(0)
  const [pendingAdmin, setPendingAdmin] = useState(0)
  const [todayMissing, setTodayMissing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [tourOpen, setTourOpen] = useState(false)
  const [background, setBackground] = useState(null)
  const menuRef = useRef(null)
  const location = useLocation()
  const navigate = useNavigate()
  const expense = hasFeature(user, 'expense')
  const split = canUseSplits(user)
  const splitPlan = hasFeature(user, 'split')
  const subscription = hasFeature(user, 'subscription')
  const accounting = hasFeature(user, 'accounting')

  // Welcome tour: once per login session until the user ticks "Don't show again"
  useEffect(() => {
    if (user.show_tour && !sessionStorage.getItem('tour-seen')) setTourOpen(true)
    const start = () => setTourOpen(true)
    window.addEventListener('start-tour', start)
    return () => window.removeEventListener('start-tour', start)
  }, [user.show_tour])

  const closeTour = async (dontShowAgain) => {
    setTourOpen(false)
    sessionStorage.setItem('tour-seen', '1')
    if (dontShowAgain && user.show_tour) {
      const res = await api.patch('/profile/preferences', { show_tour: false })
      updateUser(res.data)
    }
  }

  // Custom background picture (Settings → Appearance); fetched with the token, so it's loaded as a blob
  useEffect(() => {
    if (!user.background_updated_at) return setBackground(null)
    let url
    api
      .get('/profile/background', { responseType: 'blob' })
      .then((r) => setBackground((url = URL.createObjectURL(r.data))))
      .catch(() => setBackground(null))
    return () => url && URL.revokeObjectURL(url)
  }, [user.background_updated_at])

  useEffect(() => {
    const close = (e) => menuRef.current && !menuRef.current.contains(e.target) && setMenuOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  useEffect(() => setMenuOpen(false), [location.pathname])

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const calls = [api.get('/notifications/unread-count'), expense ? api.get('/expenses/day-status') : null]
        // Without the plan, Split Bills shows only while they're in a group, which others can change at any time
        if (!splitPlan) api.get('/auth/me').then((r) => !cancelled && r.data.in_split_group !== user.in_split_group && updateUser(r.data)).catch(() => {})
        if (user.role === 'ADMIN') {
          calls.push(api.get('/admin/users', { params: { status_filter: 'PENDING' } }))
          calls.push(api.get('/admin/username-requests', { params: { status_filter: 'PENDING' } }))
        }
        const [n, d, pu, pr] = await Promise.all(calls)
        if (cancelled) return
        setUnread(n.data.count)
        setPendingAdmin((pu?.data.length || 0) + (pr?.data.length || 0))
        const missing = !!d && !d.data.has_entries
        setTodayMissing(missing)
        const key = `reminded-${todayISO()}`
        if (missing && new Date().getHours() >= REMIND_AFTER_HOUR && !localStorage.getItem(key) && 'Notification' in window) {
          if (Notification.permission === 'default') await Notification.requestPermission()
          if (Notification.permission === 'granted') {
            new Notification('Expense Tracker', { body: "You haven't added today's expenses yet." })
            localStorage.setItem(key, '1')
          }
        }
      } catch {
        /* ignore polling errors */
      }
    }
    check()
    const id = setInterval(check, 5 * 60 * 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [location.pathname, user.role, expense, splitPlan, user.in_split_group])

  const toggleTheme = async () => {
    const res = await api.patch('/profile/preferences', { theme: user.theme === 'dark' ? 'light' : 'dark' })
    updateUser(res.data)
  }

  const sections = [
    {
      title: 'Overview',
      items: [
        ...(expense
          ? [
              ['/', 'Overview', SpaceDashboardOutlined, 'nav-dashboard'],
              ['/daily', 'Daily Entry', EditCalendarOutlined, 'nav-daily'],
              ['/expenses', 'Expenses', ReceiptLongOutlined, 'nav-expenses'],
            ]
          : []),
        ...(hasFeature(user, 'upi') ? [['/upi', 'UPI Tracker', PhonelinkRingOutlined, 'nav-upi']] : []),
        ...(split ? [['/splits', 'Split Bills', CallSplitOutlined, 'nav-splits']] : []),
        ...(subscription ? [['/subscriptions', 'Subscriptions', EventRepeatOutlined, 'nav-subscriptions']] : []),
      ],
    },
    {
      title: 'Manage',
      items: expense
        ? [
            ['/items', 'My Daily Items', ChecklistOutlined, 'nav-items'],
            ['/funds', 'Balance', AccountBalanceWalletOutlined, 'nav-funds'],
            ['/reports', 'Reports', AssessmentOutlined, 'nav-reports'],
            ['/import', 'Import Records', UploadFileOutlined, 'nav-import'],
            ['/sharing', 'Team & Sharing', GroupsOutlined, 'nav-sharing'],
          ]
        : [],
    },
    {
      title: 'Accounting',
      items: accounting
        ? [
            ['/accounting/vouchers', 'Vouchers', MenuBookOutlined, 'nav-vouchers'],
            ['/accounting/ledgers', 'Chart of Accounts', AccountTreeOutlined, 'nav-ledgers'],
            ['/accounting/reports', 'Financial Reports', SummarizeOutlined, 'nav-acc-reports'],
            ['/accounting/bank', 'Bank Reconciliation', AccountBalanceOutlined, 'nav-bank'],
            ['/accounting/settings', 'Accounting Settings', TuneOutlined, 'nav-acc-settings'],
          ]
        : [],
    },
    {
      title: 'Account',
      items: [
        ['/notifications', 'Notifications', NotificationsNoneOutlined, 'nav-notifications', unread],
        ['/profile', 'Profile', PersonOutlineOutlined, 'nav-profile'],
        ['/settings', 'Settings', SettingsOutlined, 'nav-settings'],
        ...(user.role === 'ADMIN' ? [['/admin', 'Administration', AdminPanelSettingsOutlined, 'nav-admin', pendingAdmin]] : []),
        ...(user.role === 'ORG_ADMIN' ? [['/organisation', 'Organisation', BusinessOutlined, 'nav-org']] : []),
      ],
    },
  ]
  const access = ACCESS[target?.access] || ACCESS.OWNER
  // Only worth showing when there is someone else's data to switch to (team, organisation, shares; everyone for admin)
  const canSwitch = accessible.some((a) => a.id !== user.id)

  return (
    <div className={`layout ${background ? 'has-bg' : ''}`} style={background ? { backgroundImage: `url(${background})` } : undefined}>
      <aside className="sidebar">
        <div className="brand">
          <Logo size={34} />
          Expense Tracker
        </div>
        <nav className="nav">
          {sections.filter((s) => s.items.length).map((s) => (
            <div key={s.title} style={{ display: 'contents' }}>
              <div className="nav-section">{s.title}</div>
              {s.items.map(([to, label, Icon, tour, count]) => (
                <NavLink key={to} to={to} end={to === '/'} data-tour={tour} className={({ isActive }) => (isActive ? 'active' : '')}>
                  <Icon style={{ fontSize: 20 }} />
                  {label}
                  {count > 0 && <span className="pill">{count}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-chip">
            <Avatar user={user} size={36} />
            <div className="who">
              <b>{user.full_name}</b>
              <span>@{user.username}</span>
            </div>
            <button className="icon-btn" title="Log out" onClick={logout}>
              <LogoutOutlined style={{ fontSize: 18 }} />
            </button>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
          <Breadcrumbs sections={sections} />
          {canSwitch && (expense || subscription) && !location.pathname.startsWith('/splits') && (
          <div className="viewing" data-tour="view-switch">
            <VisibilityOutlined style={{ fontSize: 18 }} className="muted" />
            <select value={target?.id || ''} onChange={(e) => setViewUserId(Number(e.target.value))} aria-label="Whose data to view">
              {accessible.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id === user.id ? 'My data' : `${a.full_name} (@${a.username})`}
                </option>
              ))}
            </select>
            <span className={`badge ${access.cls}`}>{access.label}</span>
          </div>
          )}
          </div>
          <div className="topbar-actions">
            <button className="icon-btn" onClick={toggleTheme} title="Toggle dark mode">
              {user.theme === 'dark' ? <LightModeOutlined style={{ fontSize: 20 }} /> : <DarkModeOutlined style={{ fontSize: 20 }} />}
            </button>
            <Link to="/notifications" className="icon-btn" title="Notifications" data-tour="notifications">
              <NotificationsNoneOutlined style={{ fontSize: 20 }} />
              {unread > 0 && <span className="dot">{unread > 99 ? '99+' : unread}</span>}
            </Link>
            <div className="menu-wrap" ref={menuRef}>
              <button className="avatar-btn" onClick={() => setMenuOpen((o) => !o)} data-tour="user-menu" aria-label="Account menu">
                <Avatar user={user} size={38} />
              </button>
              {menuOpen && (
                <div className="menu">
                  <div className="menu-head">
                    <b>{user.full_name}</b>
                    <span>
                      @{user.username} · {ROLE_LABEL[user.role] || 'User'}
                    </span>
                  </div>
                  <button onClick={() => navigate('/profile')}>
                    <PersonOutlineOutlined style={{ fontSize: 18 }} /> Profile
                  </button>
                  <button onClick={() => navigate('/settings')}>
                    <SettingsOutlined style={{ fontSize: 18 }} /> Settings
                  </button>
                  <button onClick={() => { setMenuOpen(false); setTourOpen(true) }}>
                    <AutoAwesomeOutlined style={{ fontSize: 18 }} /> Take the tour
                  </button>
                  <button className="danger" onClick={logout}>
                    <LogoutOutlined style={{ fontSize: 18 }} /> Log out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <div className="content">
          {/* The page shows this notice right after its header */}
          <PageBannerContext.Provider
            value={
              todayMissing && location.pathname !== '/daily' ? (
                <div className="alert warn banner">
                  <WarningAmberRounded style={{ fontSize: 20 }} />
                  <span className="grow">You haven’t added any expenses for today.</span>
                  <Link to="/daily">Add them now →</Link>
                </div>
              ) : null
            }
          >
            <Outlet />
          </PageBannerContext.Provider>
        </div>
      </main>
      {tourOpen && <Tour onClose={closeTour} />}
    </div>
  )
}

// Parts of a page reached by a link (/settings#upi, /import/history, /splits/12) and what to call them
const SUBPAGES = { history: 'Import history' }
const HASHES = {
  '/settings': { accounts: 'Bank accounts', budgets: 'Budgets & alerts', categories: 'Categories', aliases: 'Transaction aliases', upi: 'UPI tracker' },
}

/** Home › menu section › page › part of the page, built from the sidebar menu so the two always agree. */
function Breadcrumbs({ sections }) {
  const location = useLocation()
  const [search] = useSearchParams()
  const pages = sections.flatMap((s) => s.items.map(([to, label]) => ({ to, label, section: s.title })))
  if (!pages.length) return null
  const path = location.pathname.replace(/\/+$/, '') || '/'
  const page =
    pages.find((p) => p.to === path) ||
    pages.filter((p) => p.to !== '/' && path.startsWith(`${p.to}/`)).sort((a, b) => b.to.length - a.to.length)[0]
  if (!page) return null

  const home = pages[0]
  const crumbs = []
  if (page.section !== page.label) crumbs.push({ label: page.section })
  crumbs.push({ label: page.label, to: page.to })
  if (path !== page.to) {
    const rest = path.slice(page.to.length + 1)
    crumbs.push({ label: SUBPAGES[rest] || (/^\d+$/.test(rest) && page.to === '/splits' ? 'Group' : rest) })
  }
  const month = search.get('month')
  if (path === '/' && /^\d{4}-\d{2}$/.test(month || '')) {
    crumbs.push({ label: new Date(`${month}-01T00:00`).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }) })
  }
  const part = HASHES[path]?.[location.hash.slice(1)]
  if (part) crumbs.push({ label: part })

  return (
    <nav aria-label="Breadcrumb">
      <ol className="breadcrumb">
        <li>
          <Link to={home.to} title={`Home (${home.label})`} aria-label="Home">
            <HomeOutlined style={{ fontSize: 16 }} />
          </Link>
        </li>
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1
          return (
            <li key={`${c.label}-${i}`}>
              <ChevronRightRounded className="sep" style={{ fontSize: 16 }} />
              {last ? (
                <span aria-current="page">{c.label}</span>
              ) : c.to ? (
                <Link to={c.to}>{c.label}</Link>
              ) : (
                <span className="plain">{c.label}</span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
