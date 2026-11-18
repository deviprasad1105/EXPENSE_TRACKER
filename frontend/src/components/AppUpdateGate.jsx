import { useEffect, useState } from 'react'
import { registerPlugin } from '@capacitor/core'
import { Download, RefreshCw, ShieldAlert, X } from 'lucide-react'
import { APP_APK_URL, APP_DOWNLOAD_URL, checkAppUpdate, isApp } from '../api'
import Logo from './Logo'

// Native side: android/app/src/main/java/com/expensetracker/app/update/AppUpdatePlugin.java (app 1.4 and later)
const AppUpdate = isApp ? registerPlugin('AppUpdate') : null
const LATER_KEY = 'app-update-later'

/**
 * Android app only. When a newer app is published (mobile-app/version.json):
 * - an optional update shows a banner ("Later" hides it until the app is opened again);
 * - a required one (this version is below minVersionCode) covers the whole app until it is updated.
 * "Update now" downloads the APK inside the app and opens Android's installer; Android asks once to allow installs
 * from Expense Tracker. If that isn't possible, the APK opens in the browser instead.
 */
export default function AppUpdateGate() {
  const [update, setUpdate] = useState(null)
  const [hidden, setHidden] = useState(() => !!sessionStorage.getItem(LATER_KEY))
  const [state, setState] = useState({ step: 'idle' }) // idle | downloading | allow | installing | error
  const [percent, setPercent] = useState(null)

  useEffect(() => {
    if (!isApp) return undefined
    const check = () => checkAppUpdate().then(setUpdate)
    check()
    // Check again when the app comes back to the front (e.g. after the installer or the settings page)
    const onVisible = () => document.visibilityState === 'visible' && check()
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  useEffect(() => {
    if (!AppUpdate) return undefined
    let handle
    AppUpdate.addListener('progress', (p) => setPercent(p.percent)).then((h) => (handle = h)).catch(() => {})
    return () => handle?.remove()
  }, [])

  if (!update || (hidden && !update.required)) return null

  const openInBrowser = () => window.open(APP_DOWNLOAD_URL, '_system')

  const install = async () => {
    setPercent(null)
    setState({ step: 'downloading' })
    try {
      await AppUpdate.downloadAndInstall({ url: `${APP_APK_URL}?v=${update.latest.versionCode}` })
      setState({ step: 'installing' })
    } catch (err) {
      if (err?.code === 'NOT_ALLOWED') setState({ step: 'allow' })
      else if (err?.code === 'UNIMPLEMENTED' || /not implemented/i.test(err?.message || '')) {
        // An app without in-app updates: download in the browser
        openInBrowser()
        setState({ step: 'idle' })
      }
      else setState({ step: 'error', text: err?.message || 'The update couldn’t be downloaded.' })
    }
  }

  const later = () => {
    sessionStorage.setItem(LATER_KEY, '1')
    setHidden(true)
  }

  const { latest, required } = update
  const busy = state.step === 'downloading'

  const actions = (
    <div className="app-update-actions">
      {state.step === 'allow' ? (
        <>
          <button className="btn" onClick={() => AppUpdate.openInstallSettings().then(() => setState({ step: 'idle' }))}>
            <ShieldAlert size={16} /> Allow updates
          </button>
          <span className="app-update-hint">
            Turn on <b>Allow from this source</b> for Expense Tracker, come back, and tap <b>Update now</b> again.
          </span>
        </>
      ) : (
        <button className="btn" onClick={install} disabled={busy}>
          {busy ? <RefreshCw size={16} className="spin" /> : <Download size={16} />}
          {busy ? (percent >= 0 && percent !== null ? `Downloading… ${percent}%` : 'Downloading…') : state.step === 'installing' ? 'Install again' : 'Update now'}
        </button>
      )}
      {!required && !busy && (
        <button className="btn ghost" onClick={later}>
          Later
        </button>
      )}
    </div>
  )

  const message = (
    <>
      {state.step === 'installing' && <p className="app-update-note">Android’s installer is open: tap <b>Update</b>. You stay signed in.</p>}
      {state.step === 'error' && (
        <p className="app-update-note error">
          {state.text}{' '}
          <button type="button" className="link-button" onClick={openInBrowser}>Download in the browser instead</button>
        </p>
      )}
    </>
  )

  if (required) {
    return (
      <div className="app-update-blocker" role="dialog" aria-modal="true" aria-labelledby="app-update-title">
        <div className="app-update-card">
          <Logo size={64} />
          <h2 id="app-update-title">Update required</h2>
          <p>
            This version of Expense Tracker is no longer supported. Install version <b>{latest.versionName}</b> to keep using the app. Your data is
            safe and you stay signed in.
          </p>
          {actions}
          {message}
        </div>
      </div>
    )
  }

  return (
    <div className="app-update-banner" role="status">
      <div className="grow">
        <b>New version {latest.versionName} is available</b> with the latest features.
        {message}
      </div>
      {actions}
      {!busy && (
        <button className="icon-btn ghost app-update-close" onClick={later} aria-label="Later" title="Later">
          <X size={18} />
        </button>
      )}
    </div>
  )
}
