import { Download, Smartphone } from 'lucide-react'
import { APP_VERSION } from '../api'
import { APP_DOWNLOAD_URL } from '../appUpdate'

/** Website only (Profile): where to get the Android app. The APK downloads only when the button is tapped. */
export default function GetAndroidApp() {
  return (
    <div className="card" id="android-app">
      <div className="card-head">
        <div>
          <h3>
            <Smartphone size={18} style={{ verticalAlign: '-3px', marginRight: 6 }} />
            Android app
          </h3>
          <p className="muted">
            Version {APP_VERSION.versionName}. The same features as the website, plus auto-tracking from bank SMS and payment notifications.
          </p>
        </div>
      </div>
      <ol className="muted" style={{ margin: '0 0 14px', paddingLeft: 20, lineHeight: 1.7 }}>
        <li>On your Android phone, tap <b>Download the app</b>.</li>
        <li>Open the downloaded file. Android asks once to allow installs from your browser: allow it.</li>
        <li>Open Expense Tracker and sign in with the same account.</li>
      </ol>
      <a className="btn" href={APP_DOWNLOAD_URL} rel="noopener noreferrer">
        <Download size={16} /> Download the app
      </a>
    </div>
  )
}
