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
  if (everywhere) {
    await api.delete('/autotrack/device-token')
    setWanted(false)
  }
  if (AutoTrack) await AutoTrack.configure({ enabled: false, sms: false, notifications: false, clearToken: true })
}

// Remembered on the phone: the user turned auto-tracking on here, so it is linked again after signing back in
const WANTED = 'autotrack-wanted'
function setWanted(on) {
  try {
    if (on) localStorage.setItem(WANTED, '1')
    else localStorage.removeItem(WANTED)
  } catch {
    /* storage blocked: they turn it on again by hand */
  }
}
const wanted = () => {
  try {
    return localStorage.getItem(WANTED) === '1'
  } catch {
    return false
  }
}

/**
 * One tap: turn auto-tracking on (recording automatically), link this phone for SMS and bank-app notifications,
 * and ask Android for everything at once (SMS, notifications). Returns the phone's status; the caller then opens
 * whatever Android only allows from its settings screens (openSettingsLeft).
 */
export async function turnOnEverything() {
  const { data: current } = await api.get('/autotrack/settings')
  await api.put('/autotrack/settings', { enabled: true, mode: current.enabled ? current.mode : 'AUTO', since: current.since || null })
  await linkPhone({ sms: true, notifications: true })
  setWanted(true)
  return AutoTrack.requestAll()
}

/**
 * The two things Android only allows from its own screens: reading bank-app notifications (Notification access)
 * and not being put to sleep (battery). Both open together: the battery question on top, then Notification access.
 */
export async function openSettingsLeft(status) {
  if (!status.notificationAccess) await AutoTrack.openNotificationAccess()
  if (!status.batteryFree) await AutoTrack.requestBatteryFree()
}

/**
 * When the app opens: send anything waiting, and link the phone again if the user had auto-tracking on here but
 * signed out meanwhile (signing out unlinks the phone).
 */
export async function resumeOnOpen() {
  if (!AutoTrack) return
  const status = await AutoTrack.status()
  if (!status.linked && wanted() && status.sms) {
    const { data } = await api.get('/autotrack/settings')
    if (data.enabled) await linkPhone({ sms: true, notifications: true })
  }
  await AutoTrack.flush()
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
