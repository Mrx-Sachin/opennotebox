"use client"

import { useEffect, useRef } from "react"
import { updateReadingProgress } from "@/services/api"
import { useReader } from "@/stores/reader"

/**
 * Remember where the reader is, so a document reopens where it was left.
 *
 * Bookmarks already existed and are a different thing: a bookmark is
 * something you deliberately mark and expect to find again. Resuming has to
 * be passive - it must cost nothing and require no decision at the moment you
 * stop reading, which is precisely when nobody presses a button. So this
 * writes on its own, and the user never sees it happen.
 *
 * Debounced rather than sent per page: scrolling through a 555-page book
 * crosses dozens of pages in seconds, and each one would otherwise be a write.
 * The last position is the only one worth keeping, so coalescing loses
 * nothing.
 */
const DEBOUNCE_MS = 1500

export function useReadingProgress(docId: number | null) {
  const currentPage = useReader((s) => s.currentPage)
  // Without this gate, opening a document saved its restored-from position
  // straight back as page 1: the reader mounts at page 1, and if the restore
  // lands after the debounce (a big PDF, a slow phone) the save wins and the
  // user's place is gone. Observed exactly that on a 28-page file.
  const restoredFor = useReader((s) => s.progressRestoredFor)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSaved = useRef<{ docId: number; page: number } | null>(null)

  useEffect(() => {
    if (docId == null || !currentPage) return
    if (restoredFor !== docId) return
    if (lastSaved.current?.docId === docId && lastSaved.current.page === currentPage) return

    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      lastSaved.current = { docId, page: currentPage }
      // Deliberately unawaited and error-swallowed: this is bookkeeping over
      // the top of what the user is actually doing, and a failed save must
      // never produce an error state on the document they are reading. The
      // cost of losing one is a few pages of accuracy.
      updateReadingProgress(docId, currentPage).catch(() => {})
    }, DEBOUNCE_MS)

    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [docId, currentPage, restoredFor])

  // Closing the tab mid-page is the single most likely way to end a reading
  // session, and it is exactly the case a debounce would drop. sendBeacon is
  // the only request the browser guarantees to finish during unload - but it
  // cannot carry an Authorization header, so fall back to a synchronous best
  // effort through the normal client instead of pretending it worked.
  useEffect(() => {
    if (docId == null) return
    const flush = () => {
      const { currentPage: page, progressRestoredFor } = useReader.getState()
      if (!page || progressRestoredFor !== docId) return
      if (lastSaved.current?.docId === docId && lastSaved.current.page === page) return
      lastSaved.current = { docId, page }
      updateReadingProgress(docId, page).catch(() => {})
    }
    // visibilitychange fires on mobile app-switching, where pagehide/unload
    // often do not - and a phone is where a session most often just ends.
    const onHide = () => {
      if (document.visibilityState === "hidden") flush()
    }
    document.addEventListener("visibilitychange", onHide)
    window.addEventListener("pagehide", flush)
    return () => {
      document.removeEventListener("visibilitychange", onHide)
      window.removeEventListener("pagehide", flush)
      flush()
    }
  }, [docId])
}
