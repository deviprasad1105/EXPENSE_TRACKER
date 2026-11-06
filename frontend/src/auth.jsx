import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import api from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [accessible, setAccessible] = useState([])
  // Whose data is being viewed; kept per tab so a page refresh doesn't reset it
  const [viewUserId, setViewUserIdState] = useState(() => Number(sessionStorage.getItem('view-user')) || null)
  // Set when the server can't be reached: the session is kept so a refresh never logs the user out
  const [serverDown, setServerDown] = useState(false)

  const setViewUserId = useCallback((id) => {
    setViewUserIdState(id)
    if (id) sessionStorage.setItem('view-user', id)
    else sessionStorage.removeItem('view-user')
  }, [])

  const loadMe = useCallback(async () => {
    if (!localStorage.getItem('token')) {
      setLoading(false)
      return
    }
    try {
      const [me, acc] = await Promise.all([api.get('/auth/me'), api.get('/auth/accessible-users')])
      setUser(me.data)
      setAccessible(acc.data)
      setServerDown(false)
      setViewUserIdState((cur) => {
        const next = cur && acc.data.some((a) => a.id === cur) ? cur : me.data.id
        sessionStorage.setItem('view-user', next)
        return next
      })
    } catch (err) {
      if (err.response?.status === 401) {
        // The token really is invalid or expired: sign out
        localStorage.removeItem('token')
        setUser(null)
      } else {
        // Backend down or unreachable: keep the token and let the user retry
        setServerDown(true)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadMe()
  }, [loadMe])

  useEffect(() => {
    document.documentElement.dataset.theme = user?.theme || localStorage.getItem('theme') || 'light'
    if (user?.theme) localStorage.setItem('theme', user.theme)
  }, [user?.theme])

  // Merge a fresh copy of the user (e.g. returned by a profile update) into state
  const updateUser = (next) => {
    setUser((cur) => ({ ...cur, ...next }))
    setAccessible((list) => list.map((a) => (a.id === next.id ? { ...a, ...next } : a)))
  }

  const login = async (username, password) => {
    const form = new URLSearchParams({ username, password })
    const res = await api.post('/auth/login', form)
    localStorage.setItem('token', res.data.access_token)
    setLoading(true)
    await loadMe()
  }

  const logout = () => {
    localStorage.removeItem('token')
    sessionStorage.removeItem('tour-seen')
    setUser(null)
    setAccessible([])
    setViewUserId(null)
  }

  // The user whose data is currently being viewed (self, a child, someone who shared, or anyone for admin)
  const target = useMemo(
    () => accessible.find((a) => a.id === viewUserId) || (user && { ...user, access: 'OWNER', writable: true }),
    [accessible, viewUserId, user],
  )
  const params = useMemo(() => (target && user && target.id !== user.id ? { user_id: target.id } : {}), [target, user])

  return (
    <AuthContext.Provider
      value={{ user, loading, serverDown, login, logout, accessible, target, params, setViewUserId, refresh: loadMe, updateUser }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)

/** Features ("subscriptions") the admin grants per user: 'expense', 'split', 'subscription' or 'accounting'.
 * Admins have all of them. Accounting is built from expense tracking, so it needs that too. */
export const hasFeature = (user, feature) =>
  !!user &&
  (user.role === 'ADMIN' ||
    (!!user[`feature_${feature}`] && (!['accounting', 'upi'].includes(feature) || !!user.feature_expense)))
/** Split bills can be used by anyone added to a group, but only users with the plan can create groups. */
export const canUseSplits = (user) => hasFeature(user, 'split') || !!user?.in_split_group
