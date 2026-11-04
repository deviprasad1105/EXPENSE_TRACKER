import { BellRing, FileSpreadsheet, PieChart, Users } from 'lucide-react'
import Logo from './Logo'

export default function AuthShell({ children }) {
  return (
    <div className="auth-page">
      <section className="auth-side">
        <div className="brand" style={{ padding: 0 }}>
          <Logo size={34} />
          Expense Tracker
        </div>
        <div>
          <h1>Know where every rupee goes.</h1>
          <p>Log your daily spending in seconds, track your balance, and stay on top of your team’s expenses.</p>
          <div className="auth-feats">
            <div><PieChart /> Dashboard with your balance and spending charts</div>
            <div><BellRing /> Reminders when you forget to log a day</div>
            <div><FileSpreadsheet /> Excel and PDF reports in one click</div>
            <div><Users /> Team view for managers, plus read-only sharing</div>
          </div>
        </div>
        <span style={{ opacity: 0.6, fontSize: 12 }}>© {new Date().getFullYear()} Expense Tracker</span>
      </section>
      <section className="auth-main">{children}</section>
    </div>
  )
}
