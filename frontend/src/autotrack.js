import { registerPlugin } from '@capacitor/core'
import api, { isApp, SITE_ORIGIN } from './api'

/**
 * The phone side of auto-tracking (android/app/src/main/java/com/expensetracker/app/autotrack): reading bank SMS and
 * bank-app notifications, and sending them to the server in the background. Only exists inside the Android app.
 */
export const AutoTrack = isApp ? registerPlugin('AutoTrack') : null

export async function phoneStatus() {
  return AutoTrack ? AutoTrack.status() : null
}

/** Link this phone (a new token only it holds) and choose what it reads. */
export async function linkPhone({ sms = true, notifications = false } = {}) {
  const { data } = await api.post('/autotrack/device-token')
  return AutoTrack.configure({ apiBase: SITE_ORIGIN, token: data.token, enabled: true, sms, notifications })
}

export async function setPhoneSources({ enabled, sms, notifications }) {
  return AutoTrack.configure({ apiBase: SITE_ORIGIN, enabled, sms, notifications })
}

/** Stop reading on this phone and forget its token (the server's copy is removed too when `everywhere`). */
export async function unlinkPhone({ everywhere = false } = {}) {
  if (everywhere) await api.delete('/autotrack/device-token')
  if (AutoTrack) await AutoTrack.configure({ enabled: false, sms: false, notifications: false, clearToken: true })
}

/**
 * Read bank SMS already on the phone between two dates and send them in batches. `onProgress(done, total)`.
 * Returns the counts per status ({ CREATED: 3, PENDING: 12, SEEN: 40, … }) and how many SMS were looked at.
 */
export async function scanInbox(fromISO, toISO, onProgress) {
  const since = new Date(`${fromISO}T00:00:00`).getTime()
  const until = new Date(`${toISO}T23:59:59`).getTime()
  const { messages, scanned, limited } = await AutoTrack.readInbox({ since, until })
  const counts = {}
  for (let i = 0; i < messages.length; i += 300) {
    const { data } = await api.post('/autotrack/messages', { messages: messages.slice(i, i + 300) })
    for (const [k, v] of Object.entries(data.counts)) counts[k] = (counts[k] || 0) + v
    onProgress?.(Math.min(i + 300, messages.length), messages.length)
  }
  return { counts, found: messages.length, scanned, limited }
}
