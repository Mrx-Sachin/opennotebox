"use client"

import { ReactNode, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { getMe } from "@/services/api"
import { useAuth } from "@/stores/auth"
import ErrorBoundary from "@/components/layout/ErrorBoundary"
import PodcastBottomBar from "@/components/library/PodcastBottomBar"
import BookBottomBar from "@/components/tts/BookBottomBar"

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter()
  const [authorized, setAuthorized] = useState(false)
  const user = useAuth((state) => state.user)
  const setUser = useAuth((state) => state.setUser)

  useEffect(() => {
    const token = localStorage.getItem("token")
    if (!token) {
      const next = window.location.pathname + window.location.search
      router.replace(`/login?next=${encodeURIComponent(next)}`)
      return
    }
    setAuthorized(true)
    if (!user) {
      getMe().then(setUser).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!authorized) {
    return <div className="min-h-screen bg-void" />
  }

  return (
    <ErrorBoundary>
      {children}
      <PodcastBottomBar />
      <BookBottomBar />
    </ErrorBoundary>
  )
}
