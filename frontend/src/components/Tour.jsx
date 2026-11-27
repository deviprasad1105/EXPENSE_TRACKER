import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft, ArrowRight, BookOpen, Building2, CalendarClock, Eye, FileUp, Home, LayoutDashboard, PartyPopper, PenLine, Plus, ReceiptText,
  RotateCcw, Settings, ShieldCheck, Smartphone, Split, Users, Volume2, VolumeX, Wallet, BarChart3,
} from 'lucide-react'
import { canUseSplits, hasFeature, useAuth } from '../auth'

/**
 * The welcome tour. Each step: a short clip of the screen in action (recorded from a demo account with made-up data),
 * a few lines of text, and the same read aloud. Only the features the user's plan includes are shown; with every
 * feature, every step is. `need` is a plan feature, `role` a user role, `when` a condition from the context.
 */
const STEPS = [
  { id: 'welcome', video: 'welcome', icon: Home, title: 'Welcome to Expense Tracker',
    body: 'A short tour of what you can do here, with a clip of each screen. It takes about two minutes; skip it any time and replay it from your profile menu.',
    say: (c) => `Hi ${c.firstName}! Welcome to Expense Tracker. Let me show you around. It takes about two minutes, and you can skip it at any time.` },
  { id: 'quickadd', need: 'expense', target: 'quick-add', video: 'quickadd', icon: Plus, title: 'Add an expense in seconds',
    body: 'Press Add from any page: type the amount, tap a category, and save. Switch to “Money in” for salary or a refund. On a phone, Add is the big button in the middle of the bottom bar.',
    say: 'The quickest way to record spending: press Add from any page, type the amount, tap a category and save. Switch to money in for your salary or a refund.' },
  { id: 'dashboard', need: 'expense', target: 'nav-dashboard', video: 'dashboard', icon: LayoutDashboard, title: 'Overview',
    body: 'This month at a glance: what you spent against last month, your budget, upcoming renewals, spending by category and plain-language insights like “Food is up 30%”.',
    say: 'The overview shows this month at a glance: your spending compared with last month, your budget, spending by category, and simple insights.' },
  { id: 'daily', need: 'expense', target: 'nav-daily', video: 'daily', icon: PenLine, title: 'Daily Entry',
    body: 'The things you buy every day are filled in for you. Untick what you didn’t buy, add extras, then Confirm & Save. Set your usual items under My Daily Items.',
    say: 'Daily entry fills in the things you buy every day. Untick what you did not buy, add anything extra, and save.' },
  { id: 'expenses', need: 'expense', target: 'nav-expenses', video: 'expenses', icon: ReceiptText, title: 'Expenses',
    body: 'Every transaction, money sent and received. Filter by period or category, search, and edit any entry at any time.',
    say: 'Expenses lists every transaction. Filter, search, and edit any entry at any time.' },
  { id: 'balance', need: 'expense', target: 'nav-funds', video: 'balance', icon: Wallet, title: 'Balance',
    body: 'Add the money you receive, like your salary. Your available balance is everything received minus everything spent, per account and in total.',
    say: 'Balance is where you add the money you receive. Your available balance is everything received minus everything spent.' },
  { id: 'import', need: 'expense', target: 'nav-import', video: 'import', icon: FileUp, title: 'Import a bank statement',
    body: 'Upload a statement (PDF, Excel, CSV, even password-protected). A few guided steps: check the columns, name your payees, fix or auto-analyse problem rows, and import. Records already imported are skipped.',
    say: 'You can import a bank statement, even a password protected one. A few guided steps check the columns and your payees, fix any problem rows, and import. Nothing is imported twice.' },
  { id: 'reports', need: 'expense', target: 'nav-reports', video: 'reports', icon: BarChart3, title: 'Reports',
    body: 'Download a summary with charts and a detailed list, as Excel or PDF, for any period.',
    say: 'Reports lets you download a summary and a detailed list as Excel or PDF, for any period.' },
  { id: 'upi', need: 'upi', target: 'nav-upi', video: 'upi', icon: Smartphone, title: 'UPI Tracker',
    body: 'Your UPI payments are recorded from your bank’s SMS alerts, forwarded automatically from your phone. Payees get the names you give them.',
    say: 'The UPI tracker records your UPI payments from your bank’s text alerts, forwarded automatically from your phone.' },
  { id: 'splits', need: 'split', target: 'nav-splits', video: 'splits', icon: Split, title: 'Split Bills',
    body: 'Share bills with friends or flatmates: equal shares, percentages or fixed amounts. See who owes whom and record payments to settle up.',
    say: 'Split bills lets you share costs with friends. Split equally, by percentage or by amount, see who owes whom, and settle up.' },
  { id: 'subscriptions', need: 'subscription', target: 'nav-subscriptions', video: 'subscriptions', icon: CalendarClock, title: 'Bills & Subscriptions',
    body: 'Subscriptions that charge you automatically (Netflix, gym) and bills you pay yourself (electricity, mobile, rent, EMIs), each with a reminder before it is due.',
    say: 'Bills and Subscriptions keeps track of everything you pay again and again, and reminds you before each one is due.' },
  { id: 'accounting', need: 'accounting', target: 'nav-vouchers', video: 'accounting', icon: BookOpen, title: 'Accounting',
    body: 'Proper double-entry books: vouchers, ledgers, trial balance, profit & loss and balance sheet, GST and bank reconciliation. Your expenses post here automatically.',
    say: 'Accounting gives you proper double entry books, with ledgers, financial reports and bank reconciliation. Your expenses post here automatically.' },
  { id: 'sharing', need: 'expense', target: 'nav-sharing', icon: Users, title: 'Team & Sharing',
    body: 'Share your records read-only with someone you trust, and see the people who report to you.',
    say: 'Team and sharing lets you share your records, read only, with someone you trust.' },
  { id: 'view', when: (c) => c.canSwitch, target: 'view-switch', icon: Eye, title: 'Whose data you’re viewing',
    body: 'Switch between your own data and the people who shared theirs with you. A badge shows when it’s view-only.',
    say: 'Use this switch to look at data other people shared with you.' },
  { id: 'admin', role: 'ADMIN', target: 'nav-admin', icon: ShieldCheck, title: 'Administration',
    body: 'Approve new users, choose the features each one can use, and manage backups.',
    say: 'As an administrator, you approve new users and choose the features each one can use.' },
  { id: 'org', role: 'ORG_ADMIN', target: 'nav-org', icon: Building2, title: 'Organisation',
    body: 'Manage the people in your organisation and what they can see.',
    say: 'Organisation is where you manage the people in your organisation.' },
  { id: 'settings', target: 'nav-settings', video: 'settings', icon: Settings, title: 'Make it yours',
    body: 'Dark mode, a background picture, your bank accounts, budgets and alerts, categories and payee names, all in Settings.',
    say: 'In settings you can switch to dark mode, add your bank accounts, set budgets and alerts, and more.' },
  { id: 'done', target: 'user-menu', icon: PartyPopper, title: 'You’re all set!',
    body: 'Notifications remind you when you haven’t added the day’s spending. You can replay this tour any time from your profile menu (top right).',
    say: (c) => `That's it, ${c.firstName}! You can replay this tour any time from your profile menu. Happy tracking!` },
]

const PAD = 6
const DRAWER_QUERY = '(max-width: 1024px)'

function visible(el) {
  if (!el) return false
  if (el.checkVisibility) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
  return el.offsetParent !== null
}

function useRect(target, step) {
  const [rect, setRect] = useState(null)
  const measure = useCallback(() => {
    const el = target && document.querySelector(`[data-tour="${target}"]`)
    if (!visible(el)) return setRect(null)
    el.scrollIntoView({ block: 'nearest' })
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) return setRect(null)
    setRect({ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 })
  }, [target])
  useLayoutEffect(() => {
    measure()
    const t = setTimeout(measure, 260) // after the sidebar finishes animating
    window.addEventListener('resize', measure)
    return () => {
      clearTimeout(t)
      window.removeEventListener('resize', measure)
    }
  }, [measure, step])
  return rect
}

// ----- voice: the browser's own speech, an Indian English voice when there is one -----
function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() || []
  return voices.find((v) => v.lang === 'en-IN') || voices.find((v) => /en-GB/.test(v.lang)) || voices.find((v) => /^en/.test(v.lang)) || null
}
function speak(text, onEnd) {
  const synth = window.speechSynthesis
  if (!synth) return
  synth.cancel()
  const u = new SpeechSynthesisUtterance(text)
  const v = pickVoice()
  if (v) u.voice = v
  u.lang = v?.lang || 'en-IN'
  u.rate = 1
  u.onend = onEnd
  u.onerror = onEnd
  synth.speak(u)
}
const readPref = (k, d) => {
  try {
    const v = localStorage.getItem(k)
    return v === null ? d : v === '1'
  } catch {
    return d
  }
}
const writePref = (k, v) => {
  try {
    localStorage.setItem(k, v ? '1' : '0')
  } catch {
    /* not remembered */
  }
}

/** The tour. `onClose(dontShowAgain)` when finished or skipped; `canSwitch`: there is someone else's data to view. */
export default function Tour({ onClose, canSwitch = false }) {
  const { user } = useAuth()
  const ctx = useMemo(() => ({ firstName: (user.full_name || user.username || '').split(' ')[0], canSwitch }), [user, canSwitch])
  const steps = useMemo(
    () =>
      STEPS.filter((s) => {
        if (s.need === 'split') return canUseSplits(user)
        if (s.need === 'upi') return hasFeature(user, 'upi') && hasFeature(user, 'expense')
        if (s.need === 'accounting') return hasFeature(user, 'accounting') && hasFeature(user, 'expense')
        if (s.need && !hasFeature(user, s.need)) return false
        if (s.role && user.role !== s.role) return false
        if (s.when && !s.when(ctx)) return false
        return true
      }),
    [user, ctx],
  )
  const [i, setI] = useState(0)
  const [dontShow, setDontShow] = useState(false)
  const [voiceOn, setVoiceOn] = useState(() => readPref('tour-voice', true) && 'speechSynthesis' in window)
  const [speaking, setSpeaking] = useState(false)
  const [videoFailed, setVideoFailed] = useState({})
  const [drawer, setDrawer] = useState(() => window.matchMedia(DRAWER_QUERY).matches)
  const videoRef = useRef(null)
  const step = steps[i]
  const rect = useRect(drawer ? null : step.target, i)
  const last = i === steps.length - 1
  const say = typeof step.say === 'function' ? step.say(ctx) : step.say || step.body
  const Icon = step.icon || Home

  useEffect(() => {
    const mq = window.matchMedia(DRAWER_QUERY)
    const change = () => setDrawer(mq.matches)
    mq.addEventListener('change', change)
    return () => mq.removeEventListener('change', change)
  }, [])

  // Read each step aloud (voices load late in some browsers: retry once they are there)
  useEffect(() => {
    if (!voiceOn || !window.speechSynthesis) return
    const go = () => {
      setSpeaking(true)
      speak(say, () => setSpeaking(false))
    }
    const t = setTimeout(go, 250)
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.speaking || go()
    return () => {
      clearTimeout(t)
      window.speechSynthesis.onvoiceschanged = null
    }
  }, [i, voiceOn, say])
  useEffect(() => () => window.speechSynthesis?.cancel(), [])

  const close = useCallback(() => {
    window.speechSynthesis?.cancel()
    onClose(dontShow)
  }, [onClose, dontShow])
  const go = useCallback((n) => setI((cur) => Math.max(0, Math.min(steps.length - 1, cur + n))), [steps.length])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') close()
      if (e.key === 'ArrowRight') last ? close() : go(1)
      if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, last, close])

  const toggleVoice = () => {
    const next = !voiceOn
    setVoiceOn(next)
    writePref('tour-voice', next)
    if (!next) {
      window.speechSynthesis?.cancel()
      setSpeaking(false)
    }
  }
  const replay = () => {
    setSpeaking(true)
    speak(say, () => setSpeaking(false))
    if (videoRef.current) {
      videoRef.current.currentTime = 0
      videoRef.current.play().catch(() => {})
    }
  }

  // Beside the highlighted menu item on wide screens; centred otherwise
  const placed = rect && !drawer
  const style = placed
    ? (() => {
        const w = Math.min(600, window.innerWidth - rect.left - rect.width - 48)
        const h = Math.min(window.innerHeight - 32, 640)
        const top = Math.max(16, Math.min(rect.top + rect.height / 2 - h / 2, window.innerHeight - h - 16))
        return w >= 380 ? { top, left: rect.left + rect.width + 24, width: w } : null
      })()
    : null
  const hasVideo = step.video && !videoFailed[step.video]

  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-label="Product tour">
      <div className="tour-spot" style={rect || { top: '50%', left: '50%', width: 0, height: 0, outline: 'none' }} />
      {rect && style && <span className="tour-pointer" style={{ top: rect.top + rect.height / 2, left: rect.left + rect.width + 8 }} />}
      <div className={`tour-card ${style ? 'placed' : 'centred'}`} style={style || undefined}>
        <div className="tour-media">
          {hasVideo ? (
            <video
              ref={videoRef}
              key={step.video}
              src={`/tour/${step.video}.webm`}
              autoPlay
              muted
              loop
              playsInline
              onError={() => setVideoFailed((f) => ({ ...f, [step.video]: true }))}
            />
          ) : (
            <div className="tour-art">
              <span><Icon size={64} strokeWidth={1.6} /></span>
            </div>
          )}
          <span className="tour-step-chip">
            <Icon size={16} /> {i + 1} / {steps.length}
          </span>
        </div>
        <div className="tour-body">
          <div className="tour-progress" aria-hidden="true">
            {steps.map((s, idx) => (
              <button key={s.id} className={idx < i ? 'done' : idx === i ? 'on' : ''} onClick={() => setI(idx)} tabIndex={-1} title={s.title} />
            ))}
          </div>
          <h3>{step.title}</h3>
          <p>{step.body}</p>
          <div className="tour-voice">
            <button className={`btn ghost small ${voiceOn ? 'on' : ''}`} onClick={toggleVoice} aria-pressed={voiceOn}
                    disabled={!('speechSynthesis' in window)} title={voiceOn ? 'Turn the voice off' : 'Read the tour aloud'}>
              {voiceOn ? <Volume2 size={16} /> : <VolumeX size={16} />} {voiceOn ? 'Voice on' : 'Voice off'}
              {voiceOn && speaking && <span className="tour-wave" aria-hidden="true"><i /><i /><i /></span>}
            </button>
            <button className="btn ghost small" onClick={replay} title="Play this step again">
              <RotateCcw size={15} /> Replay
            </button>
          </div>
          <div className="tour-foot">
            <label className="check">
              <input type="checkbox" checked={dontShow} onChange={(e) => setDontShow(e.target.checked)} />
              Don’t show again
            </label>
            <div className="actions">
              {!last && <button className="btn ghost small" onClick={close}>Skip tour</button>}
              {i > 0 && (
                <button className="btn secondary small" onClick={() => go(-1)}>
                  <ArrowLeft size={15} /> Back
                </button>
              )}
              <button className="btn small" onClick={() => (last ? close() : go(1))} autoFocus>
                {last ? 'Start using the app' : 'Next'} {!last && <ArrowRight size={15} />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
