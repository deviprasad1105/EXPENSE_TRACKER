// The Android app is published in the repository's mobile-app/ folder by the "Publish Android app" workflow.
// The APK downloads only when the person taps a download button: neither the website nor the app downloads or
// installs an APK by itself (a site that pushes app files is what Google Safe Browsing flags as dangerous).
import { APP_VERSION, isApp } from './api'

const REPO = 'https://github.com/deviprasad1105/EXPENSE_TRACKER'
// The APK itself: tapping a link to it downloads the file straight away
export const APP_DOWNLOAD_URL = `${REPO}/raw/main/mobile-app/ExpenseTracker.apk`
// Published next to the APK: the newest app's version, and the oldest one still allowed
const VERSION_URL = 'https://raw.githubusercontent.com/deviprasad1105/EXPENSE_TRACKER/main/mobile-app/version.json'

/**
 * Inside the app: { latest: { versionCode, versionName }, required } when a newer app is published, else null.
 * `required` is true when this version is older than `minVersionCode`: it must be updated before it can be used.
 */
export async function checkAppUpdate() {
  if (!isApp) return null
  try {
    const res = await fetch(`${VERSION_URL}?t=${Date.now()}`, { cache: 'no-store' })
    if (!res.ok) return null
    const latest = await res.json()
    if (!(Number(latest.versionCode) > APP_VERSION.versionCode)) return null
    return { latest, required: APP_VERSION.versionCode < Number(latest.minVersionCode || 0) }
  } catch {
    return null
  }
}
