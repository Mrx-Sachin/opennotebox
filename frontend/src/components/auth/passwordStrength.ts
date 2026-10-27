/** Lightweight, non-blocking strength guidance for the register form.
    This is advisory only - the backend's only real requirement is 6-72
    characters (see UserCreate in schemas.py) - self-hosted single/small-
    team deployments don't need onerous composition rules, just a nudge. */
export type PasswordStrength = "empty" | "weak" | "fair" | "good"

export function estimatePasswordStrength(password: string): PasswordStrength {
  if (!password) return "empty"
  let score = 0
  if (password.length >= 8) score++
  if (password.length >= 12) score++
  if (/[0-9]/.test(password) && /[a-zA-Z]/.test(password)) score++
  if (/[^a-zA-Z0-9]/.test(password)) score++
  if (score <= 1) return "weak"
  if (score <= 2) return "fair"
  return "good"
}

export const STRENGTH_LABEL: Record<PasswordStrength, string> = {
  empty: "",
  weak: "Weak - consider a longer or more varied password",
  fair: "Fair",
  good: "Good",
}

export const STRENGTH_COLOR: Record<PasswordStrength, string> = {
  empty: "bg-surface",
  weak: "bg-rust",
  fair: "bg-yellow-500",
  good: "bg-emerald-500",
}
