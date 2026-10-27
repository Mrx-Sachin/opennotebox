"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Loader2, TriangleAlert } from "lucide-react"
import { login, getMe } from "@/services/api"
import { useAuth } from "@/stores/auth"
import AuthCard from "@/components/auth/AuthCard"
import PasswordField from "@/components/auth/PasswordField"
import { describeAuthError } from "@/components/auth/authError"

/** Where to send the user after login: the page they were trying to reach
    (?next=...) if there was one, otherwise the dashboard. Restricted to an
    in-app path so a crafted next= can't bounce someone off to an external
    site (open-redirect). */
function safeNext(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw
  return "/dashboard"
}

export default function LoginPage() {
  const router = useRouter()
  const setUser = useAuth((s) => s.setUser)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Already signed in and just landed here (e.g. stale tab) - skip the form.
  useEffect(() => {
    if (typeof window !== "undefined" && localStorage.getItem("token")) {
      const next = safeNext(new URLSearchParams(window.location.search).get("next"))
      router.replace(next)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError("")
    setSubmitting(true)
    try {
      const data = await login(username, password)
      localStorage.setItem("token", data.access_token)
      localStorage.setItem("refresh_token", data.refresh_token)
      try {
        setUser(await getMe())
      } catch {
        // Non-fatal - the signed-in-as badge just won't have a name yet;
        // it'll pick one up next time getMe() succeeds.
      }
      const next = safeNext(new URLSearchParams(window.location.search).get("next"))
      router.push(next)
    } catch (err) {
      setError(describeAuthError(err, "login"))
      setSubmitting(false)
    }
  }

  return (
    <AuthCard title="OpenNoteBox" subtitle="Sign in to your library">
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
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-lg border border-surface bg-void px-3 py-2.5 text-sm text-cream placeholder:text-cream-dim/60 transition-colors focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo/40"
          />
        </div>

        <PasswordField
          id="password"
          label="Password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-rust/30 bg-rust/10 px-3 py-2 text-xs text-rust">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100"
        >
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {submitting ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-center text-sm text-cream-dim">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-indigo hover:underline">
            Register
          </Link>
        </p>
      </form>
    </AuthCard>
  )
}
