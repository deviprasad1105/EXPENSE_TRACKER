import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import AccountBalanceOutlined from '@mui/icons-material/AccountBalanceOutlined'
import AccountBalanceWalletOutlined from '@mui/icons-material/AccountBalanceWalletOutlined'
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined'
import AdminPanelSettingsOutlined from '@mui/icons-material/AdminPanelSettingsOutlined'
import AssessmentOutlined from '@mui/icons-material/AssessmentOutlined'
import SmsOutlined from '@mui/icons-material/SmsOutlined'
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
import AndroidRounded from '@mui/icons-material/AndroidRounded'
import ChevronRightRounded from '@mui/icons-material/ChevronRightRounded'
import CloseRounded from '@mui/icons-material/CloseRounded'
import ChevronLeftRounded from '@mui/icons-material/ChevronLeftRounded'
import MenuRounded from '@mui/icons-material/MenuRounded'
import PhonelinkRingOutlined from '@mui/icons-material/PhonelinkRingOutlined'
import ReceiptLongOutlined from '@mui/icons-material/ReceiptLongOutlined'
import SettingsOutlined from '@mui/icons-material/SettingsOutlined'
import SpaceDashboardOutlined from '@mui/icons-material/SpaceDashboardOutlined'
import SummarizeOutlined from '@mui/icons-material/SummarizeOutlined'
import TuneOutlined from '@mui/icons-material/TuneOutlined'
import UploadFileOutlined from '@mui/icons-material/UploadFileOutlined'
import VisibilityOutlined from '@mui/icons-material/VisibilityOutlined'
import WarningAmberRounded from '@mui/icons-material/WarningAmberRounded'
import api, { APP_DOWNLOAD_URL, isApp, todayISO } from '../api'
import { AutoTrack } from '../autotrack'
import { canUseSplits, hasFeature, useAuth } from '../auth'
import Avatar from './Avatar'
import Tour from './Tour'
import Logo from './Logo'
import QuickAdd from './QuickAdd'
import AddRounded from '@mui/icons-material/AddRounded'
import MoreHorizRounded from '@mui/icons-material/MoreHorizRounded'
import { PageBannerContext, PageIconContext } from './ui'

const ACCESS = {
  OWNER: { label: 'Your data', cls: 'ok' },
  ADMIN: { label: 'Admin · full access', cls: 'info' },
  MANAGER: { label: 'Your team · view only', cls: 'warn' },
  SHARED: { label: 'Shared · view only', cls: 'warn' },
  ORG: { label: 'Your organisation · view only', cls: 'warn' },
}
const ROLE_LABEL = { ADMIN: 'Administrator', ORG_ADMIN: 'Organisation admin', USER: 'User' }
const REMIND_AFTER_HOUR = 18
// At this width and below (tablets, phones) the sidebar is a drawer opened from the top bar
const DRAWER_QUERY = '(max-width: 1024px)'

function storedCollapsed() {
  try {
    return localStorage.getItem('sidebar-collapsed') === '1'
  } catch {
    return false
  }
}

export default function Layout() {
  const { user, logout, accessible, target, setViewUserId, updateUser } = useAuth()
  const [unread, setUnread] = useState(0)
  const [pendingAdmin, setPendingAdmin] = useState(0)
  const [todayMissing, setTodayMissing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [tourOpen, setTourOpen] = useState(false)
  const [background, setBackground] = useState(null)
  // Desktop: the sidebar can be narrowed to its icons (remembered). Tablet / phone: a drawer, closed by default.
  const [collapsed, setCollapsed] = useState(storedCollapsed)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [quickAdd, setQuickAdd] = useState(false)
  // "You haven't added any expenses for today" can be put away for the rest of the day
  const [nudgeHidden, setNudgeHidden] = useState(() => {
    try {
      return localStorage.getItem('nudge-hidden') === todayISO()
    } catch {
      return false
    }
  })
  const hideNudge = () => {
    setNudgeHidden(true)
    try {
      localStorage.setItem('nudge-hidden', todayISO())
    } catch {
      /* not remembered */
    }
  }
  const [isDrawer, setIsDrawer] = useState(() => window.matchMedia(DRAWER_QUERY).matches)
  const menuRef = useRef(null)
  const location = useLocation()
  const navigate = useNavigate()
  const expense = hasFeature(user, 'expense')
  const split = canUseSplits(user)
  const splitPlan = hasFeature(user, 'split')
  const subscription = hasFeature(user, 'subscription')
  const accounting = hasFeature(user, 'accounting')

  // Android app: send bank messages waiting on the phone, and open the page a tapped auto-tracking notification asked for
  useEffect(() => {
    if (!AutoTrack) return undefined
    const go = () => AutoTrack.takeRoute().then(({ route }) => route && navigate(route)).catch(() => {})
    go()
    AutoTrack.flush().catch(() => {})
    window.addEventListener('autotrackRoute', go)
    return () => window.removeEventListener('autotrackRoute', go)
  }, [navigate])

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
  useEffect(() => {
    setMenuOpen(false)
    setDrawerOpen(false)
  }, [location.pathname])

  useEffect(() => {
    const mq = window.matchMedia(DRAWER_QUERY)
    const change = () => {
      setIsDrawer(mq.matches)
      if (!mq.matches) setDrawerOpen(false)
    }
    mq.addEventListener('change', change)
    return () => mq.removeEventListener('change', change)
  }, [])

  // The open drawer: Escape closes it and the page behind doesn't scroll
  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e) => e.key === 'Escape' && setDrawerOpen(false)
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [drawerOpen])

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    try {
      localStorage.setItem('sidebar-collapsed', next ? '1' : '0')
    } catch {
      /* private mode: not remembered */
    }
  }
  const narrow = collapsed && !isDrawer
  // The collapsed menu names each icon in a tooltip beside it (outside the scrolling list, so it isn't clipped)
  const [tip, setTip] = useState(null)
  const showTip = (label) => (e) => {
    if (!narrow) return
    const r = e.currentTarget.getBoundingClientRect()
    setTip({ label, top: r.top + r.height / 2, left: r.right + 12 })
  }
  const hideTip = () => setTip(null)
  useEffect(hideTip, [narrow, location.pathname])

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
        ...(hasFeature(user, 'upi')
          ? [
              ['/auto-tracking', 'Auto-tracking', SmsOutlined, 'nav-autotrack'],
              ['/upi', 'UPI Tracker', PhonelinkRingOutlined, 'nav-upi'],
            ]
          : []),
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
  // The page being shown (its menu entry): its icon is shown large beside the page title
  const path = location.pathname.replace(/\/+$/, '') || '/'
  const pageIcon = sections
    .flatMap((s) => s.items)
    .filter(([to]) => to === path || (to !== '/' && path.startsWith(`${to}/`)))
    .sort((a, b) => b[0].length - a[0].length)[0]?.[2]
  const access = ACCESS[target?.access] || ACCESS.OWNER
  const canAdd = expense && !!target?.writable
  // The bottom bar on phones: up to four main pages (with Add in the middle)
  const bottomItems = sections.flatMap((s) => s.items)
    .filter(([to]) => ['/', '/expenses', '/funds', '/reports', '/splits', '/notifications'].includes(to))
    .slice(0, 4)
  // Only worth showing when there is someone else's data to switch to (team, organisation, shares; everyone for admin)
  const canSwitch = accessible.some((a) => a.id !== user.id)

  return (
    <div
      className={`layout ${background ? 'has-bg' : ''} ${narrow ? 'sidebar-collapsed' : ''} ${drawerOpen ? 'drawer-open' : ''}`}
      style={background ? { backgroundImage: `url(${background})` } : undefined}
    >
      {drawerOpen && <div className="drawer-back" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}
      <aside className="sidebar" id="app-sidebar" aria-label="Main menu" {...(isDrawer && !drawerOpen ? { inert: '' } : {})}>
        <div className="brand">
          <Link to="/" className="brand-link" aria-label="Go to Overview" onClick={() => setDrawerOpen(false)}>
            <Logo size={34} />
            <span className="brand-name">Expense Tracker</span>
          </Link>
          {isDrawer && (
            <button className="icon-btn drawer-close" onClick={() => setDrawerOpen(false)} aria-label="Close menu">
              <CloseRounded style={{ fontSize: 20 }} />
            </button>
          )}
        </div>
        {!isDrawer && (
          <button className="edge-toggle" onClick={toggleCollapsed} aria-label={collapsed ? 'Expand the menu' : 'Collapse the menu'}
                  aria-expanded={!collapsed} aria-controls="app-sidebar" onMouseEnter={showTip(collapsed ? 'Expand menu' : null)} onMouseLeave={hideTip}>
            <ChevronLeftRounded style={{ fontSize: 18 }} />
          </button>
        )}
        <nav className="nav">
          {sections.filter((s) => s.items.length).map((s) => (
            <div key={s.title} style={{ display: 'contents' }}>
              <div className="nav-section"><span className="nav-label">{s.title}</span></div>
              {s.items.map(([to, label, Icon, tour, count]) => (
                <NavLink key={to} to={to} end={to === '/'} data-tour={tour} className={({ isActive }) => (isActive ? 'active' : '')}
                         aria-label={narrow ? label : undefined} onMouseEnter={showTip(label)} onMouseLeave={hideTip}
                         onFocus={showTip(label)} onBlur={hideTip}>
                  <Icon style={{ fontSize: 20 }} />
                  <span className="nav-label">{label}</span>
                  {count > 0 && <span className="pill">{count}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-chip">
            <Avatar user={user} size={36} />
            <div className="who nav-label">
              <b>{user.full_name}</b>
              <span>@{user.username}</span>
            </div>
            <button className="icon-btn nav-label" title="Log out" onClick={logout}>
              <LogoutOutlined style={{ fontSize: 18 }} />
            </button>
          </div>
        </div>
      </aside>
      {tip?.label && narrow && (
        <div className="nav-tip" role="tooltip" style={{ top: tip.top, left: tip.left }}>{tip.label}</div>
      )}

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
          {isDrawer && (
            <button className="icon-btn menu-toggle" onClick={() => setDrawerOpen(true)} aria-label="Open menu" aria-controls="app-sidebar" aria-expanded={drawerOpen}>
              <MenuRounded style={{ fontSize: 22 }} />
            </button>
          )}
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
            {/* Already in the app: no point offering to download it */}
            {!isApp && (
              <a href={APP_DOWNLOAD_URL} className="icon-btn" title="Download the Android app" aria-label="Download the Android app">
                <AndroidRounded style={{ fontSize: 22, color: '#3ddc84' }} />
              </a>
            )}
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
              todayMissing && !nudgeHidden && location.pathname !== '/daily' ? (
                <div className="alert warn banner">
                  <WarningAmberRounded style={{ fontSize: 20 }} />
                  <span className="grow">You haven’t added any expenses for today.</span>
                  {canAdd && <button className="link-btn strong" onClick={() => setQuickAdd(true)}>Add one now</button>}
                  <Link to="/daily">Daily entry →</Link>
                  <button className="icon-btn nudge-close" onClick={hideNudge} title="Hide for today" aria-label="Hide for today">×</button>
                </div>
              ) : null
            }
          >
            <PageIconContext.Provider value={pageIcon || null}>
              <Outlet />
            </PageIconContext.Provider>
          </PageBannerContext.Provider>
        </div>
      </main>
      {canAdd && (
        <button className="fab" data-tour="quick-add" onClick={() => setQuickAdd(true)} aria-label="Quick add an expense or money received" title="Quick add">
          <AddRounded style={{ fontSize: 30 }} />
          <span>Add</span>
        </button>
      )}
      {/* Phones: the main places one tap away, and Add in the middle */}
      <nav className="bottom-nav" aria-label="Main">
        {bottomItems.map(([to, label, Icon]) => (
          <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
            <Icon style={{ fontSize: 24 }} />
            <span>{label}</span>
          </NavLink>
        )).reduce((acc, el, i) => (i === 2 && canAdd ? [...acc, (
          <button key="add" className="bottom-add" onClick={() => setQuickAdd(true)} aria-label="Quick add">
            <AddRounded style={{ fontSize: 30 }} />
          </button>
        ), el] : [...acc, el]), [])}
        <button className={drawerOpen ? 'active' : ''} onClick={() => setDrawerOpen(true)} aria-label="More pages">
          <MoreHorizRounded style={{ fontSize: 24 }} />
          <span>More</span>
        </button>
      </nav>
      {quickAdd && <QuickAdd onClose={() => setQuickAdd(false)} />}
      {tourOpen && <Tour onClose={closeTour} canSwitch={canSwitch} />}
    </div>
  )
}

// Parts of a page reached by a link (/settings#upi, /import/history, /splits/12) and what to call them
const SUBPAGES = { history: 'Import history', purge: 'Purge records' }
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
