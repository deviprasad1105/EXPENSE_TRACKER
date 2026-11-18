import { BarChart3, BellRing, FileText, Users } from 'lucide-react'
import Logo from './Logo'

const FEATURES = [
  [BarChart3, 'Dashboard with your balance and spending charts'],
  [BellRing, 'Reminders when you forget to log a day'],
  [FileText, 'Excel and PDF reports in one click'],
  [Users, 'Team view for managers, plus read-only sharing'],
]

/** Sign-in, sign-up and recovery pages: the brand panel on the left (wide screens) and the page's card on the right. */
export default function AuthShell({ children }) {
  return (
    <div className="auth-page">
      <section className="auth-side">
        {/* Soft waves behind the content */}
        <svg className="auth-waves" viewBox="0 0 600 900" preserveAspectRatio="none" aria-hidden="true">
          <path d="M600 120C470 160 430 300 520 420s60 300-60 480h140Z" fill="url(#auth-wave-a)" />
          <path d="M0 900c120-80 260-120 400-90s170 50 200 40v50Z" fill="url(#auth-wave-b)" />
          <defs>
            <linearGradient id="auth-wave-a" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#38bdf8" stopOpacity=".35" />
              <stop offset="1" stopColor="#6366f1" stopOpacity=".05" />
            </linearGradient>
            <linearGradient id="auth-wave-b" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#818cf8" stopOpacity=".25" />
              <stop offset="1" stopColor="#38bdf8" stopOpacity=".35" />
            </linearGradient>
          </defs>
        </svg>

        <div className="auth-brand">
          <Logo size={52} />
          <span>Expense Tracker</span>
        </div>

        <div className="auth-pitch">
          <h1>
            Know where every <span className="auth-accent">rupee</span> goes.
          </h1>
          <p>Log your daily spending in seconds, track your balance, and stay on top of your team’s expenses.</p>
          <ul className="auth-feats">
            {FEATURES.map(([Icon, text]) => (
              <li key={text}>
                <span className="auth-feat-icon">
                  <Icon size={22} />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>

        <img className="auth-art" src="/logo.png" alt="" aria-hidden="true" draggable={false} />
        <span className="auth-copy">© {new Date().getFullYear()} Expense Tracker. All rights reserved.</span>
      </section>

      <section className="auth-main">
        <svg className="auth-main-waves" viewBox="0 0 900 900" preserveAspectRatio="none" aria-hidden="true">
          <path d="M900 0v260c-90-30-150-120-170-260Z" />
          <path d="M900 900V640c-120 40-180 170-200 260Z" />
          <path d="M0 900c90-60 200-70 320-20s150 20 180 20Z" />
        </svg>
        {children}
      </section>
    </div>
  )
}
