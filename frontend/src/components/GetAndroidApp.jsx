import { ExternalLink, Smartphone } from 'lucide-react'
import { APP_VERSION } from '../api'
import { APP_PAGE_URL } from '../appUpdate'

/** Website only (Profile): where to get the Android app. The download itself happens on GitHub, when the person asks. */
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
        <li>On your Android phone, open the download page and tap <b>Download</b>.</li>
        <li>Open the downloaded file. Android asks once to allow installs from your browser: allow it.</li>
        <li>Open Expense Tracker and sign in with the same account.</li>
      </ol>
      <a className="btn" href={APP_PAGE_URL} target="_blank" rel="noopener noreferrer">
        <ExternalLink size={16} /> Open the download page
      </a>
    </div>
  )
}
