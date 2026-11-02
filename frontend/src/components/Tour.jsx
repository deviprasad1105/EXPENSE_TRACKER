import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'

export const TOUR_STEPS = [
  { target: null, title: 'Welcome to Expense Tracker', body: 'A quick tour of the main features. It takes about a minute, and you can skip it at any time.' },
  { target: 'nav-dashboard', title: 'Dashboard', body: 'Your available balance, what you spent today and this period, and charts of your spending.' },
  { target: 'nav-daily', title: 'Daily Entry', body: 'Add the day’s expenses here. Your daily items are already filled in, so you can adjust them, add extras, and Confirm & Save. You can edit saved entries for 3 days.' },
  { target: 'nav-items', title: 'My Daily Items', body: 'Set up what you buy every day, like cigarettes, chocolate or food. Constant items keep a fixed price, and Custom items let you change the amount each day.' },
  { target: 'nav-funds', title: 'Balance', body: 'Add your salary or pocket money here. Your available balance is the funds you add minus everything you spend.' },
  { target: 'nav-reports', title: 'Reports', body: 'Download a summary and a detailed list of your expenses as Excel or PDF, for any date range.' },
  { target: 'nav-splits', title: 'Split Bills', body: 'Share bills with a group: split by equal shares, percentages or fixed amounts, see who owes whom, and record payments to settle up.' },
  { target: 'nav-sharing', title: 'Team & Sharing', body: 'See who reports to you and share your data read-only with other people.' },
  { target: 'view-switch', title: 'Whose data you are viewing', body: 'Switch to the data of your team or of people who shared with you. A badge shows when the data is view-only.' },
  { target: 'notifications', title: 'Notifications', body: 'Reminders when you forgot to add a day’s expenses, plus approvals and shares.' },
  { target: 'user-menu', title: 'Your profile', body: 'Change your photo, username and password here. In Settings you can turn this tour back on or switch to dark mode.' },
]

const PAD = 6
const CARD_W = 340

function useRect(target, step) {
  const [rect, setRect] = useState(null)
  const measure = useCallback(() => {
    const el = target && document.querySelector(`[data-tour="${target}"]`)
    if (!el || el.offsetParent === null) return setRect(null)
    el.scrollIntoView({ block: 'nearest' })
    const r = el.getBoundingClientRect()
    setRect({ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 })
  }, [target])
  useLayoutEffect(() => {
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure, step])
  return rect
}

function cardPosition(rect) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  if (!rect) return { top: vh / 2 - 120, left: vw / 2 - CARD_W / 2 }
  const clampX = (x) => Math.max(16, Math.min(x, vw - CARD_W - 16))
  const clampY = (y) => Math.max(16, Math.min(y, vh - 240))
  if (rect.left + rect.width + CARD_W + 24 < vw) return { top: clampY(rect.top), left: rect.left + rect.width + 16 }
  if (rect.top + rect.height + 260 < vh) return { top: rect.top + rect.height + 12, left: clampX(rect.left + rect.width - CARD_W) }
  return { top: clampY(rect.top - 230), left: clampX(rect.left) }
}

/** Spotlight walkthrough. `onClose(dontShowAgain)` is called when finished or skipped. */
export default function Tour({ onClose }) {
  const [i, setI] = useState(0)
  const [dontShow, setDontShow] = useState(false)
  const step = TOUR_STEPS[i]
  const rect = useRect(step.target, i)
  const last = i === TOUR_STEPS.length - 1

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose(dontShow)
      if (e.key === 'ArrowRight' && !last) setI(i + 1)
      if (e.key === 'ArrowLeft' && i > 0) setI(i - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [i, last, dontShow, onClose])

  const pos = cardPosition(rect)
  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-label="Product tour">
      <div
        className="tour-spot"
        style={rect || { top: '50%', left: '50%', width: 0, height: 0, outline: 'none' }}
      />
      <div className="tour-card" style={pos}>
        <div className="tour-progress">
          {TOUR_STEPS.map((_, idx) => (
            <i key={idx} className={idx <= i ? 'on' : ''} />
          ))}
        </div>
        <h3>
          {i === 0 && <Sparkles size={18} color="var(--primary)" />}
          {step.title}
        </h3>
        <p>{step.body}</p>
        <div className="tour-foot">
          <label className="check">
            <input type="checkbox" checked={dontShow} onChange={(e) => setDontShow(e.target.checked)} />
            Don’t show again
          </label>
          <div className="actions">
            {!last && (
              <button className="btn ghost small" onClick={() => onClose(dontShow)}>
                Skip
              </button>
            )}
            {i > 0 && (
              <button className="btn secondary small" onClick={() => setI(i - 1)}>
                Back
              </button>
            )}
            <button className="btn small" onClick={() => (last ? onClose(dontShow) : setI(i + 1))}>
              {last ? 'Finish' : `Next (${i + 1}/${TOUR_STEPS.length})`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
