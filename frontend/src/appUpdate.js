// Android app only. Kept out of api.js so the website's bundle carries no link to the APK: AppUpdateGate (the only
// user) is loaded lazily, and only inside the app (see App.jsx). A file offered for download from a site with a
// sign-in page is what Google Safe Browsing flags as deceptive.
import { APP_VERSION, isApp } from './api'

// The Android app, kept in the repository's mobile-app/ folder (public, so anyone can download it)
export const APP_DOWNLOAD_URL = 'https://github.com/deviprasad1105/EXPENSE_TRACKER/raw/main/mobile-app/ExpenseTracker.apk'
// Published next to the APK by `npm run publish:apk`: the newest app's version, and the oldest one still allowed
const APP_RAW = 'https://raw.githubusercontent.com/deviprasad1105/EXPENSE_TRACKER/main/mobile-app'
// The APK as the app downloads it itself (in-app update), without GitHub's redirect page
export const APP_APK_URL = `${APP_RAW}/ExpenseTracker.apk`

/**
 * Inside the app: { latest: { versionCode, versionName }, required } when a newer app is published, else null.
 * `required` is true when this version is older than `minVersionCode`: it must be updated before it can be used.
 */
export async function checkAppUpdate() {
  if (!isApp) return null
  try {
    const res = await fetch(`${APP_RAW}/version.json?t=${Date.now()}`, { cache: 'no-store' })
    if (!res.ok) return null
    const latest = await res.json()
    if (!(Number(latest.versionCode) > APP_VERSION.versionCode)) return null
    return { latest, required: APP_VERSION.versionCode < Number(latest.minVersionCode || 0) }
  } catch {
    return null
  }
}
