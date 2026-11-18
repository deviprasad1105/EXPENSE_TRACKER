/**
 * App mark: receipt, wallet, coins and a rising chart on a violet-to-cyan tile (public/logo.png).
 * The Android launcher icon and splash are made from the same art (frontend/assets/, `npx @capacitor/assets generate`).
 */
export default function Logo({ size = 32, className = '' }) {
  return (
    <img className={`logo ${className}`.trim()} src="/logo.png" width={size} height={size} style={{ width: size, height: size }}
      alt="Expense Tracker" draggable={false} />
  )
}
