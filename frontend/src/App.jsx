import { Navigate, Route, Routes } from 'react-router-dom'
import { canUseSplits, hasFeature, useAuth } from './auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import DailyEntry from './pages/DailyEntry'
import Expenses from './pages/Expenses'
import DailyItems from './pages/DailyItems'
import Funds from './pages/Funds'
import Reports from './pages/Reports'
import Sharing from './pages/Sharing'
import Notifications from './pages/Notifications'
import Admin from './pages/Admin'
import Organisation from './pages/Organisation'
import Profile from './pages/Profile'
import Settings from './pages/Settings'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Splits from './pages/Splits'
import SplitGroup from './pages/SplitGroup'
import Subscriptions from './pages/Subscriptions'
import Vouchers from './accounting/Vouchers'
import Ledgers from './accounting/Ledgers'
import AccountingReports from './accounting/Reports'
import BankReconciliation from './accounting/Bank'
import AccountingSettings from './accounting/AccountingSettings'

function Protected({ children, adminOnly, orgAdminOnly }) {
  const { user, loading, serverDown, refresh, logout } = useAuth()
  if (loading) return <div className="center">Loading…</div>
  if (!user && serverDown)
    return (
      <div className="center">
        <div className="card" style={{ maxWidth: 420, textAlign: 'center' }}>
          <h3>Can’t reach the server</h3>
          <p className="muted">The backend at /api isn’t responding. You’re still signed in, so try again once it’s running.</p>
          <div className="actions" style={{ justifyContent: 'center' }}>
            <button className="btn" onClick={refresh}>Retry</button>
            <button className="btn secondary" onClick={logout}>Sign out</button>
          </div>
        </div>
      </div>
    )
  if (!user) return <Navigate to="/login" replace />
  if (adminOnly && user.role !== 'ADMIN') return <Navigate to="/" replace />
  if (orgAdminOnly && user.role !== 'ORG_ADMIN') return <Navigate to="/" replace />
  return children
}

/** Pages of a feature the user's plan doesn't include send them to one it does. */
function Feature({ name, children }) {
  const { user } = useAuth()
  if (name === 'split' ? canUseSplits(user) : hasFeature(user, name)) return children
  if (name === 'expense' && canUseSplits(user)) return <Navigate to="/splits" replace />
  if (name === 'expense' && hasFeature(user, 'subscription')) return <Navigate to="/subscriptions" replace />
  return <NoFeatures />
}

function NoFeatures() {
  return (
    <div className="card" style={{ maxWidth: 520 }}>
      <h3>Nothing is enabled for your account yet</h3>
      <p className="muted">Ask an administrator to add expense tracking, split bills or subscriptions to your plan.</p>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route index element={<Feature name="expense"><Dashboard /></Feature>} />
        <Route path="daily" element={<Feature name="expense"><DailyEntry /></Feature>} />
        <Route path="expenses" element={<Feature name="expense"><Expenses /></Feature>} />
        <Route path="items" element={<Feature name="expense"><DailyItems /></Feature>} />
        <Route path="funds" element={<Feature name="expense"><Funds /></Feature>} />
        <Route path="reports" element={<Feature name="expense"><Reports /></Feature>} />
        <Route path="sharing" element={<Feature name="expense"><Sharing /></Feature>} />
        <Route path="splits" element={<Feature name="split"><Splits /></Feature>} />
        <Route path="splits/:groupId" element={<Feature name="split"><SplitGroup /></Feature>} />
        <Route path="subscriptions" element={<Feature name="subscription"><Subscriptions /></Feature>} />
        <Route path="accounting/vouchers" element={<Feature name="accounting"><Vouchers /></Feature>} />
        <Route path="accounting/ledgers" element={<Feature name="accounting"><Ledgers /></Feature>} />
        <Route path="accounting/reports" element={<Feature name="accounting"><AccountingReports /></Feature>} />
        <Route path="accounting/bank" element={<Feature name="accounting"><BankReconciliation /></Feature>} />
        <Route path="accounting/settings" element={<Feature name="accounting"><AccountingSettings /></Feature>} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="profile" element={<Profile />} />
        <Route path="settings" element={<Settings />} />
        <Route
          path="admin"
          element={
            <Protected adminOnly>
              <Admin />
            </Protected>
          }
        />
        <Route
          path="organisation"
          element={
            <Protected orgAdminOnly>
              <Organisation />
            </Protected>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
