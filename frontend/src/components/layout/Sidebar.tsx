"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import { Plus, FileText, ChevronsRight, X, UsersRound } from "lucide-react"
import { useReader } from "@/stores/reader"
import { Document, User } from "@/services/api"
import Link from "next/link"
import { useRouter } from "next/navigation"

interface SidebarProps {
  documents: Document[]
  loading: boolean
  onDelete: (id: number) => void
  user?: User | null
}

// The dashboard used to poll GET /api/documents every 5s (now only while jobs
// are active), which sorts newest-first.
// During a multi-file upload, each doc lands at a slightly different time and
// jumps straight to the top, shoving every row below it down a slot. A human
// clicking where they *think* their book is can land on whatever just got
// pushed into that spot instead. To beat that race we hold the on-screen
// order stable and only let a freshly-arrived row jump to its true sorted
// position after a short quiet period - long enough to clear normal human
// reaction time, short enough that the list still feels live.
const REORDER_DELAY_MS = 500
const HIGHLIGHT_DURATION_MS = 1800

const STATUS_STYLES: Record<string, { label: string; cls: string }> = {
  completed: { label: "ready", cls: "bg-[#4ade80]/15 text-[#4ade80]" },
  failed: { label: "failed", cls: "bg-[#ef4444]/15 text-[#ef4444]" },
  pending: { label: "queued", cls: "bg-surface text-cream-dim" },
}

/** "p.29 · 5%" for a document with reading history, empty otherwise.
 *  Page number first because that is what a reader recognises; the percentage
 *  is the thing that tells them whether it is worth finishing. */
function progressLabel(doc: Document): string {
  const page = doc.last_read_page
  if (!page || page < 1) return ""
  if (!doc.num_pages) return `p.${page}`
  const pct = Math.round((page / doc.num_pages) * 100)
  // Page 1 of 555 is 0%, which reads as "you have not started" when the user
  // demonstrably has. Floor the display at 1% so any progress is visible.
  return `p.${page} · ${Math.max(pct, 1)}%`
}

function statusBadge(status: string) {
  return (
    STATUS_STYLES[status] ?? {
      label: status.replace(/_/g, " "),
      cls: "bg-indigo/15 text-indigo animate-pulse",
    }
  )
}

export default function Sidebar({ documents, loading, onDelete, user }: SidebarProps) {
  const router = useRouter()
  const { sidebarCollapsed, selectedDocId, selectDoc, setUploadModalOpen, toggleSidebar } = useReader()

  // Rendered order of doc ids. Deliberately decoupled from `documents`'
  // (server-sorted, newest-first) order so a poll can't instantly reshuffle
  // rows out from under an in-flight click - see REORDER_DELAY_MS above.
  const [displayOrder, setDisplayOrder] = useState<number[]>([])
  const [newlyArrived, setNewlyArrived] = useState<Set<number>>(new Set())
  const knownIdsRef = useRef<Set<number>>(new Set())
  const hasLoadedRef = useRef(false)
  const reorderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const highlightTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  useEffect(() => {
    const incomingIds = documents.map((d) => d.id)
    const incomingSet = new Set(incomingIds)

    // First load: nothing "arrived", just adopt the server's order as-is.
    if (!hasLoadedRef.current) {
      hasLoadedRef.current = true
      knownIdsRef.current = incomingSet
      setDisplayOrder(incomingIds)
      return
    }

    const newIds = incomingIds.filter((id) => !knownIdsRef.current.has(id))
    knownIdsRef.current = incomingSet

    setDisplayOrder((prev) => {
      // Deletions aren't part of the race (they're user-initiated, not a
      // surprise insertion) so drop vanished rows immediately.
      const stillPresent = prev.filter((id) => incomingSet.has(id))
      // New rows land at the bottom of the CURRENT view first, not wherever
      // the server's sort would put them - that way a click already headed
      // for an existing row can never be hijacked by something that just
      // appeared above it. They get promoted to their real position once
      // the reorder timer below fires.
      const arriving = newIds.filter((id) => !stillPresent.includes(id))
      return [...stillPresent, ...arriving]
    })

    if (newIds.length === 0) return

    setNewlyArrived((prev) => {
      const next = new Set(prev)
      newIds.forEach((id) => next.add(id))
      return next
    })
    newIds.forEach((id) => {
      const existingTimer = highlightTimersRef.current.get(id)
      if (existingTimer) clearTimeout(existingTimer)
      const timer = setTimeout(() => {
        highlightTimersRef.current.delete(id)
        setNewlyArrived((prev) => {
          if (!prev.has(id)) return prev
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }, HIGHLIGHT_DURATION_MS)
      highlightTimersRef.current.set(id, timer)
    })

    // Coalesce a burst of arrivals (e.g. a 3-file batch upload landing across
    // several polls) into one reorder instead of resorting per insertion:
    // each new arrival pushes the deadline back, so the promotion to true
    // sorted order only happens once things go quiet for REORDER_DELAY_MS.
    if (reorderTimerRef.current) clearTimeout(reorderTimerRef.current)
    reorderTimerRef.current = setTimeout(() => {
      reorderTimerRef.current = null
      setDisplayOrder(incomingIds)
    }, REORDER_DELAY_MS)
  }, [documents])

  useEffect(
    () => () => {
      if (reorderTimerRef.current) clearTimeout(reorderTimerRef.current)
      highlightTimersRef.current.forEach((timer) => clearTimeout(timer))
    },
    []
  )

  const docsById = useMemo(() => new Map(documents.map((d) => [d.id, d])), [documents])
  const orderedDocs = displayOrder
    .map((id) => docsById.get(id))
    .filter((d): d is Document => d !== undefined)

  return (
    <aside
      className={`reader-scroll flex shrink-0 flex-col overflow-hidden border-r border-surface bg-panel transition-[width] duration-200 ease-out ${
        // A collapsed rail is a useful affordance on a desktop and pure waste
        // on a phone: 64px of a 412px screen is 15% of the reading width spent
        // on an icon strip whose only job is duplicated by the bottom bar's
        // Library tab. Expanded, it takes the full width like the other panels.
        sidebarCollapsed ? "hidden w-16 sm:flex" : "absolute inset-y-0 left-0 z-40 w-full sm:static sm:w-[280px]"
      }`}
    >
      {sidebarCollapsed && (
        <div className="flex justify-center pt-3">
          <button
            onClick={toggleSidebar}
            title="Expand sidebar (Ctrl+B)"
            className="rounded-md p-2 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <ChevronsRight className="h-4 w-4" />
          </button>
        </div>
      )}
      <div className="p-3">
        {/* On mobile the expanded sidebar is a full-width drawer over the
            reader, so it needs its own close button (the TopBar Library
            toggle is the only other way out, which felt like friction). */}
        {!sidebarCollapsed && (
          <div className="mb-2 flex items-center sm:hidden">
            <button
              onClick={toggleSidebar}
              aria-label="Close library"
              className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        <button
          onClick={() => setUploadModalOpen(true)}
          title="Add document (Ctrl+U)"
          className={`gradient-primary flex items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.02] ${
            sidebarCollapsed ? "w-10 px-0" : "w-full px-4"
          }`}
        >
          <Plus className="h-4 w-4 shrink-0" />
          {!sidebarCollapsed && <span>Add</span>}
        </button>
        <Link
          href="/dashboard/shared"
          title="Shared library"
          aria-label="Shared library"
          className={`mt-2 flex items-center justify-center gap-2 rounded-lg border border-surface py-2 text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo ${sidebarCollapsed ? "w-10 px-0" : "w-full px-3"}`}
        >
          <UsersRound className="h-4 w-4 shrink-0" aria-hidden="true" />
          {!sidebarCollapsed && <span>Shared library</span>}
        </Link>
      </div>

      <nav className="reader-scroll flex-1 overflow-y-auto px-2 pb-3">
        {loading && documents.length === 0 ? (
          <div className="space-y-2 p-1">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded-lg bg-surface/60" />
            ))}
          </div>
        ) : documents.length === 0 && !sidebarCollapsed ? (
          <p className="px-3 py-6 text-center text-xs text-cream-dim">
            Your documents will appear here
          </p>
        ) : (
          <ul className="space-y-1">
            {orderedDocs.map((doc) => {
              const active = doc.id === selectedDocId
              const badge = statusBadge(doc.processing_status)
              const isNew = newlyArrived.has(doc.id)
              return (
                <li key={doc.id}>
                  <button
                    onClick={() => {
                      selectDoc(doc.id, doc.num_pages, doc.last_read_page ?? undefined)
                      router.push(`/dashboard/read/${doc.id}`)
                    }}
                    title={doc.title}
                    className={`group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                      active
                        ? "gradient-primary text-white shadow-glow"
                        : "text-cream-dim hover:bg-surface hover:text-cream"
                    } ${isNew ? "sidebar-row-new" : ""}`}
                  >
                    <FileText className="h-4 w-4 shrink-0" />
                    {!sidebarCollapsed && (
                      <>
                        <span className="min-w-0 flex-1 truncate text-sm">
                          {doc.title}
                          {/* How far in this document is, shown only once
                              there is real progress. A "0%" on everything
                              untouched would be noise on a shelf of books the
                              user has not opened yet, and it is exactly the
                              distinction last_read_page being nullable exists
                              to preserve. */}
                          {progressLabel(doc) && (
                            <span
                              className={`ml-1.5 whitespace-nowrap text-[10px] tabular-nums ${
                                active ? "text-white/70" : "text-cream-dim"
                              }`}
                            >
                              {progressLabel(doc)}
                            </span>
                          )}
                        </span>
                        {doc.processing_status !== "completed" && (
                          <span
                            className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${badge.cls}`}
                          >
                            {badge.label}
                          </span>
                        )}
                        <span
                          role="button"
                          tabIndex={0}
                          onClick={(e) => {
                            e.stopPropagation()
                            onDelete(doc.id)
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.stopPropagation()
                              onDelete(doc.id)
                            }
                          }}
                          title="Delete document"
                          className={`hidden shrink-0 rounded p-0.5 text-xs group-hover:block ${
                            active ? "text-white/70 hover:text-white" : "text-cream-dim hover:text-[#ef4444]"
                          }`}
                        >
                          ✕
                        </span>
                      </>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </nav>
    </aside>
  )
}
