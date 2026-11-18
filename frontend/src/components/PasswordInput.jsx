import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'

/**
 * A password field with an eye button that shows or hides what was typed.
 * Takes the same props as <input>. `type` is set by the toggle, and `className` / `style` go on the wrapper.
 */
export default function PasswordInput({ className = '', style, disabled, ...props }) {
  const [shown, setShown] = useState(false)
  const label = shown ? 'Hide password' : 'Show password'
  return (
    <div className={`password-input ${className}`.trim()} style={style}>
      <input {...props} disabled={disabled} type={shown ? 'text' : 'password'} spellCheck={false} autoCapitalize="off" autoCorrect="off" />
      <button
        type="button"
        className="password-toggle"
        onClick={() => setShown((s) => !s)}
        // Keep the focus (and the cursor position) in the field while toggling
        onMouseDown={(e) => e.preventDefault()}
        disabled={disabled}
        aria-label={label}
        aria-pressed={shown}
        title={label}
      >
        {shown ? <EyeOff size={18} /> : <Eye size={18} />}
      </button>
    </div>
  )
}
