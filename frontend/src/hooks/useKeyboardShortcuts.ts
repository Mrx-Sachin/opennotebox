"use client"

import { useEffect } from "react"
import { useReader } from "@/stores/reader"
import { useNotes } from "@/stores/notes"
import { ttsEngine } from "@/services/ttsEngine"
import { pagesFromSelection, selectionQuote } from "@/lib/textSelect"

/** Global reader shortcuts (PROJECT_DESIGN.md §12). Inactive while typing. */
export function useKeyboardShortcuts() {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      ) {
        return
      }
      const state = useReader.getState()

      if (e.ctrlKey || e.metaKey) {
        switch (e.key.toLowerCase()) {
          case "b":
            e.preventDefault()
            state.toggleSidebar()
            return
          case "m":
            e.preventDefault()
            ttsEngine.toggleMute()
            return
          case "u":
            e.preventDefault()
            state.setUploadModalOpen(true)
            return
        }
        return
      }

      switch (e.key) {
        case " ":
          if (state.selectedDocId == null) return
          e.preventDefault()
          if (state.tts.status === "idle") {
            void ttsEngine.readPage(state.selectedDocId, state.currentPage, 0)
          } else {
            ttsEngine.toggle()
          }
          return
        case "ArrowLeft":
          if (state.selectedDocId == null) return
          e.preventDefault()
          if (state.tts.status !== "idle") {
            // Podcast/summary audio seeks in time; page reading skips pages
            if (state.tts.page == null) ttsEngine.skipSeconds(-15)
            else ttsEngine.skipPage(-1)
          } else if (state.currentPage > 1) state.setPage(state.currentPage - 1)
          return
        case "ArrowRight":
          if (state.selectedDocId == null) return
          e.preventDefault()
          if (state.tts.status !== "idle") {
            if (state.tts.page == null) ttsEngine.skipSeconds(15)
            else ttsEngine.skipPage(1)
          } else if (!state.numPages || state.currentPage < state.numPages)
            state.setPage(state.currentPage + 1)
          return
        case "h":
        case "H":
        case "n":
        case "N": {
          if (e.shiftKey) return
          const notes = useNotes.getState()
          let sel = notes.selection
          if (!sel) {
            const quote = selectionQuote()
            if (!quote || state.selectedDocId == null) return
            const live = window.getSelection()
            const range = live?.rangeCount ? live.getRangeAt(0).getBoundingClientRect() : null
            const pages = live ? pagesFromSelection(live) : null
            sel = {
              docId: state.selectedDocId,
              page: pages?.start ?? state.currentPage,
              endPage: pages && pages.end !== pages.start ? pages.end : undefined,
              quote,
              x: range ? range.left + range.width / 2 : 80,
              y: range ? range.bottom : 80,
            }
            notes.setSelection(sel)
          }
          e.preventDefault()
          if (e.key.toLowerCase() === "n") {
            notes.requestCompose()
            if (!state.summaryOpen) state.toggleSummary()
            return
          }
          void notes
            .create({
              document_id: sel.docId,
              page_number: sel.page,
              end_page: sel.endPage ?? null,
              quote: sel.quote,
              color: notes.lastColor || "amber",
            })
            .then((note) => {
              notes.focusNote(note.id)
              if (!useReader.getState().summaryOpen) useReader.getState().toggleSummary()
            })
          return
        }
        case "f":
        case "F":
          if (state.selectedDocId == null) return
          e.preventDefault()
          state.toggleFocusMode()
          return
        case "1":
          state.toggleSummary()
          return
        case "2":
          state.toggleChat()
          return
        case "Escape":
          if (state.uploadModalOpen) state.setUploadModalOpen(false)
          else if (state.focusMode) state.toggleFocusMode()
          else if (state.tts.status !== "idle") ttsEngine.stop()
          return
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])
}
