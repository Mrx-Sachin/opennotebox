"use client"

import { useId, useState } from "react"
import { Eye, EyeOff } from "lucide-react"

interface PasswordFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: "current-password" | "new-password"
  minLength?: number
  maxLength?: number
  autoFocus?: boolean
  hint?: string
  hintTone?: "neutral" | "error"
}

/** Password input matching the reader's dark theme, with a show/hide toggle.
    Shared between login and register so both get the same a11y treatment:
    a real <label>, autoComplete hints password managers rely on, and a
    toggle button pulled out of tab order so Tab still goes straight from
    the field to Submit. */
export default function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  minLength,
  maxLength,
  autoFocus,
  hint,
  hintTone = "neutral",
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  const hintId = useId()

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-cream-dim">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          name={id}
          type={visible ? "text" : "password"}
          required
          minLength={minLength}
          maxLength={maxLength}
          autoFocus={autoFocus}
          autoComplete={autoComplete}
          aria-describedby={hint ? hintId : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-lg border border-surface bg-void px-3 py-2.5 pr-10 text-sm text-cream placeholder:text-cream-dim/60 transition-colors focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo/40"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          tabIndex={-1}
          title={visible ? "Hide password" : "Show password"}
          aria-label={visible ? "Hide password" : "Show password"}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-cream-dim transition-colors hover:text-cream"
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {hint && (
        <p id={hintId} className={`mt-1.5 text-[11px] ${hintTone === "error" ? "text-rust" : "text-cream-dim"}`}>
          {hint}
        </p>
      )}
    </div>
  )
}
