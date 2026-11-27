import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'
import { isApp } from '../api'
import { APP_DOWNLOAD_URL, checkAppUpdate } from '../appUpdate'
import Logo from './Logo'

const LATER_KEY = 'app-update-later'

/**
 * Android app only. When a newer app is published (mobile-app/version.json):
 * - an optional update shows a banner ("Later" hides it until the app is opened again);
 * - a required one (this version is below minVersionCode) covers the whole app until it is updated.
 * "Download update" downloads the new APK in the browser; the app itself never downloads or installs anything.
 */
export default function AppUpdateGate() {
  const [update, setUpdate] = useState(null)
  const [hidden, setHidden] = useState(() => !!sessionStorage.getItem(LATER_KEY))

  useEffect(() => {
    if (!isApp) return undefined
    const check = () => checkAppUpdate().then(setUpdate)
    check()
    // Check again when the app comes back to the front (e.g. after installing the new version)
    const onVisible = () => document.visibilityState === 'visible' && check()
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  if (!update || (hidden && !update.required)) return null

  const later = () => {
    sessionStorage.setItem(LATER_KEY, '1')
    setHidden(true)
  }

  const { latest, required } = update

  const actions = (
    <div className="app-update-actions">
      <button className="btn" onClick={() => window.open(APP_DOWNLOAD_URL, '_system')}>
        <Download size={16} /> Download update
      </button>
      {!required && (
        <button className="btn ghost" onClick={later}>
          Later
        </button>
      )}
    </div>
  )
  const how = (
    <p className="app-update-note">
      Downloads in your browser: open the file when it finishes to install it over this app. Your data is safe and you stay
      signed in.
    </p>
  )

  if (required) {
    return (
      <div className="app-update-blocker" role="dialog" aria-modal="true" aria-labelledby="app-update-title">
        <div className="app-update-card">
          <Logo size={64} />
          <h2 id="app-update-title">Update required</h2>
          <p>
            This version of Expense Tracker is no longer supported. Install version <b>{latest.versionName}</b> to keep using the app.
          </p>
          {actions}
          {how}
        </div>
      </div>
    )
  }

  return (
    <div className="app-update-banner" role="status">
      <div className="grow">
        <b>New version {latest.versionName} is available</b> with the latest features.
        {how}
      </div>
      {actions}
      <button className="icon-btn ghost app-update-close" onClick={later} aria-label="Later" title="Later">
        <X size={18} />
      </button>
    </div>
  )
}
