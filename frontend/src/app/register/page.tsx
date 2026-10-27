"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Loader2, TriangleAlert } from "lucide-react"
import { register } from "@/services/api"
import AuthCard from "@/components/auth/AuthCard"
import PasswordField from "@/components/auth/PasswordField"
import { describeAuthError } from "@/components/auth/authError"
import { estimatePasswordStrength, STRENGTH_LABEL, STRENGTH_COLOR } from "@/components/auth/passwordStrength"

export default function RegisterPage() {
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const strength = useMemo(() => estimatePasswordStrength(password), [password])
  const mismatch = confirmPassword.length > 0 && password !== confirmPassword

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    if (password !== confirmPassword) {
      setError("Passwords don't match.")
      return
    }
    setError("")
    setSubmitting(true)
    try {
      await register(username, password)
      router.push("/login")
    } catch (err) {
      setError(describeAuthError(err, "register"))
      setSubmitting(false)
    }
  }

  return (
    <AuthCard title="Create account" subtitle="Set up your OpenNoteBox library">
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="username" className="mb-1.5 block text-xs font-medium text-cream-dim">
            Username
          </label>
          <input
            id="username"
            name="username"
            type="text"
            required
            autoFocus
            minLength={3}
            maxLength={50}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-lg border border-surface bg-void px-3 py-2.5 text-sm text-cream placeholder:text-cream-dim/60 transition-colors focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo/40"
          />
        </div>

        <div>
          <PasswordField
            id="password"
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            minLength={6}
            maxLength={72}
          />
          {/* Advisory strength meter, not a hard gate - see passwordStrength.ts */}
          {password.length > 0 && (
            <div className="mt-1.5 flex items-center gap-2">
              <div className="flex h-1 flex-1 gap-1 overflow-hidden rounded-full">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className={`h-full flex-1 rounded-full transition-colors ${
                      (strength === "weak" && i === 0) ||
                      (strength === "fair" && i <= 1) ||
                      strength === "good"
                        ? STRENGTH_COLOR[strength]
                        : "bg-surface"
                    }`}
                  />
                ))}
              </div>
              <span className="whitespace-nowrap text-[11px] text-cream-dim">{STRENGTH_LABEL[strength]}</span>
            </div>
          )}
        </div>

        <PasswordField
          id="confirm-password"
          label="Confirm password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          autoComplete="new-password"
          hint={mismatch ? "Passwords don't match" : undefined}
          hintTone="error"
        />

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-rust/30 bg-rust/10 px-3 py-2 text-xs text-rust">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || mismatch}
          className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100"
        >
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {submitting ? "Creating account…" : "Create account"}
        </button>

        <p className="text-center text-sm text-cream-dim">
          Already have an account?{" "}
          <Link href="/login" className="text-indigo hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </AuthCard>
  )
}
