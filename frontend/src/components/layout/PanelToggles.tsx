"use client"

import { Library, StickyNote, Sparkles } from "lucide-react"
import { useReader } from "@/stores/reader"

/**
 * Show/hide controls for the three side panels.
 *
 * These actions already existed as Ctrl+B / S / C and nothing on screen said
 * so, which is indistinguishable from the panels not being hideable at all -
 * the reported complaint. Making them visible, with a lit state showing what
 * is currently open, is the whole point; the shortcuts stay in the tooltips
 * so the keyboard path is discoverable rather than secret.
 *
 * The same component serves the top bar on a desktop and the bottom bar on a
 * phone. One definition means the two can't drift, and on a phone the bar sits
 * at the bottom because that is where a thumb reaches.
 */
export default function PanelToggles({ size = "sm" }: { size?: "sm" | "lg" }) {
  const sidebarCollapsed = useReader((s) => s.sidebarCollapsed)
  const summaryOpen = useReader((s) => s.summaryOpen)
  const chatOpen = useReader((s) => s.chatOpen)
  const toggleSidebar = useReader((s) => s.toggleSidebar)
  const toggleSummary = useReader((s) => s.toggleSummary)
  const toggleChat = useReader((s) => s.toggleChat)

  const items = [
    {
      key: "library",
      label: "Library",
      hint: "Toggle library (Ctrl+B)",
      icon: Library,
      active: !sidebarCollapsed,
      onClick: toggleSidebar,
    },
    {
      key: "notes",
      label: "Notes",
      hint: "Toggle notes (S)",
      icon: StickyNote,
      active: summaryOpen,
      onClick: toggleSummary,
    },
    {
      key: "chat",
      label: "Assistant",
      hint: "Toggle assistant (C)",
      icon: Sparkles,
      active: chatOpen,
      onClick: toggleChat,
    },
  ]

  // 44px is the smallest reliably tappable target; the desktop bar can afford
  // to be denser because a pointer is precise.
  const big = size === "lg"

  return (
    <div
      role="group"
      aria-label="Panels"
      className={`flex items-center ${big ? "w-full justify-around" : "gap-0.5 rounded-lg bg-surface/40 p-0.5"}`}
    >
      {items.map(({ key, label, hint, icon: Icon, active, onClick }) => (
        <button
          key={key}
          type="button"
          onClick={onClick}
          title={hint}
          aria-pressed={active}
          aria-label={label}
          className={
            big
              ? `flex min-h-[3rem] min-w-[4rem] flex-1 flex-col items-center justify-center gap-0.5 rounded-lg text-[10px] font-medium transition-colors ${
                  active ? "text-indigo" : "text-cream-dim"
                }`
              : `flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? "bg-panel text-cream shadow-sm"
                    : "text-cream-dim hover:bg-surface/60 hover:text-cream"
                }`
          }
        >
          <Icon className={big ? "h-5 w-5" : "h-3.5 w-3.5"} />
          <span className={big ? "" : "hidden lg:inline"}>{label}</span>
        </button>
      ))}
    </div>
  )
}
