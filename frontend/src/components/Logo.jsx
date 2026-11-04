import { useId } from 'react'

/**
 * App mark: a rupee receipt on a violet-to-cyan tile. The same drawing is in public/favicon.svg,
 * so keep the two in step.
 */
export default function Logo({ size = 32 }) {
  const id = useId()
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Expense Tracker">
      <defs>
        <linearGradient id={`${id}-bg`} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7c3aed" />
          <stop offset="0.55" stopColor="#4f46e5" />
          <stop offset="1" stopColor="#06b6d4" />
        </linearGradient>
        <linearGradient id={`${id}-shine`} x1="0" y1="0" x2="0" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fff" stopOpacity="0.28" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id}-bg)`} />
      <rect width="32" height="32" rx="9" fill={`url(#${id}-shine)`} />
      <g transform="translate(6 6) scale(0.8333)" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z" fill="#fff" fillOpacity="0.16" />
        <path d="M8 7h8" />
        <path d="M8 11h8" />
        <path d="M12 17.5 8 15h1a4 4 0 0 0 0-8" />
      </g>
    </svg>
  )
}
