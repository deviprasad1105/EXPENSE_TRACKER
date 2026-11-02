import { useEffect, useState } from 'react'
import api from '../api'

const COLORS = ['#4f46e5', '#0e9384', '#c4320a', '#7a5af8', '#dd2590', '#1570ef', '#4e5ba6', '#099250']
const cache = new Map()

export const initials = (name = '') =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join('') || '?'

/** Profile photo, falling back to coloured initials. `version` (avatar_updated_at) busts the cache. */
export default function Avatar({ user, size = 36 }) {
  const [src, setSrc] = useState(null)
  const version = user?.avatar_updated_at
  const key = user && version ? `${user.id}:${version}` : null

  useEffect(() => {
    if (!key) return setSrc(null)
    if (cache.has(key)) return setSrc(cache.get(key))
    let alive = true
    api
      .get(`/profile/avatar/${user.id}`, { responseType: 'blob' })
      .then((r) => {
        const url = URL.createObjectURL(r.data)
        cache.set(key, url)
        if (alive) setSrc(url)
      })
      .catch(() => alive && setSrc(null))
    return () => {
      alive = false
    }
  }, [key, user?.id])

  const color = COLORS[(user?.id || 0) % COLORS.length]
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.38, background: src ? 'transparent' : color }}>
      {src ? <img src={src} alt="" /> : initials(user?.full_name || user?.username)}
    </span>
  )
}
