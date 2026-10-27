"use client"

import { ReactNode, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { getMe } from "@/services/api"
import { useAuth } from "@/stores/auth"
import ErrorBoundary from "@/components/layout/ErrorBoundary"

/** Route guard for everything under /dashboard. Previously there was none -
    the page itself polled localStorage in a useEffect, which still let the
    unauthenticated page flash briefly and only handled that one route. This
    runs before children render (via the `authorized` gate below) and
    preserves the originally-requested URL as ?next= so login sends the
    user back to where they were, not always to the dashboard root.

    Deliberately renders no UI of its own. The signed-in username and the
    sign-out control both live in TopBar, which already owned sign-out -
    surfacing a second one here put two sign-out affordances on screen at
    once and floated a badge over the reader. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter()
  const [authorized, setAuthorized] = useState(false)
  const user = useAuth((s) => s.user)
  const setUser = useAuth((s) => s.setUser)

  useEffect(() => {
    const token = localStorage.getItem("token")
    if (!token) {
      const next = window.location.pathname + window.location.search
      router.replace(`/login?next=${encodeURIComponent(next)}`)
      return
    }
    setAuthorized(true)
    if (!user) {
      // Failure here just means TopBar won't have a name yet - api.ts's
      // response interceptor already owns refresh-or-redirect for a 401,
      // so there's nothing else to do on error.
      getMe().then(setUser).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!authorized) {
    // Blank void screen instead of a flash of the (still unauthenticated)
    // dashboard while the localStorage check above resolves.
    return <div className="min-h-screen bg-void" />
  }

  return <ErrorBoundary>{children}</ErrorBoundary>
}
