"use client"

import { useState } from "react"
import { BookOpen, LogOut, Maximize2, Minimize2, Settings } from "lucide-react"
import { useReader } from "@/stores/reader"
import { useAuth } from "@/stores/auth"
import SettingsModal from "./SettingsModal"
import PanelToggles from "./PanelToggles"
import { useViewport } from "@/hooks/useViewport"

export default function TopBar() {
  const { focusMode, toggleFocusMode } = useReader()
  const viewport = useViewport()
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Populated by the dashboard route guard's getMe() call. Shown here rather
  // than in a separate floating badge: this bar already owns the sign-out
  // control, and two sign-out affordances on one screen is worse than none.
  const user = useAuth((s) => s.user)

  const handleLogout = () => {
    localStorage.removeItem("token")
    localStorage.removeItem("refresh_token")
    useAuth.getState().clear()
    window.location.href = "/login"
  }

  return (
    <header className="relative shrink-0 bg-void">
      <div className="flex h-12 items-center gap-3 px-3">
        <div className="flex shrink-0 items-center gap-2">
          <div className="gradient-primary flex h-7 w-7 items-center justify-center rounded-lg shadow-glow">
            <BookOpen className="h-4 w-4 text-white" />
          </div>
          {/* The wordmark is the first thing to go when width is scarce: it
              identifies the app, but the icon already does that, and a panel
              control earns the pixels more than a second logo does. */}
          <span className="hidden font-display text-base font-bold tracking-tight text-cream sm:inline">
            DocMind
          </span>
        </div>
        {/* Panel controls sit next to the identity on a desktop and move to a
            bottom bar on a phone (see the dashboard), so a thumb can reach
            them. Rendering them in both places at once would be two sources
            of truth for the same state. */}
        {viewport !== "phone" && (
          <div className="ml-2 shrink-0">
            <PanelToggles />
          </div>
        )}
        <div className="min-w-0 flex-1" />
        <button
          onClick={toggleFocusMode}
          title="Focus mode (F)"
          className={`rounded-md p-2 transition-colors hover:bg-surface ${
            focusMode ? "text-indigo" : "text-cream-dim hover:text-cream"
          }`}
        >
          {focusMode ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </button>
        <button
          onClick={() => setSettingsOpen(true)}
          title="Settings"
          className="rounded-md p-2 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
        >
          <Settings className="h-4 w-4" />
        </button>
        {user && (
          <span
            className="hidden max-w-[10rem] truncate pl-1 text-xs text-cream-dim md:inline"
            title={`Signed in as ${user.username}`}
          >
            {user.username}
          </span>
        )}
        <button
          onClick={handleLogout}
          title={user ? `Sign out ${user.username}` : "Sign out"}
          className="rounded-md p-2 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
      <div className="gradient-hairline absolute inset-x-0 bottom-0" />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </header>
  )
}
