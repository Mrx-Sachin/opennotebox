"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { getPages, ExtractedPage } from "@/services/api"
import { useReader } from "@/stores/reader"
import { useNotes } from "@/stores/notes"
import LoadingSpinner from "@/components/ui/LoadingSpinner"
import { ttsEngine } from "@/services/ttsEngine"
import { ChevronLeft, ChevronRight, List, X, Moon, Sun } from "lucide-react"
import { splitHighlightedText } from "@/lib/noteHighlights"
import { colorHex, hexToRgba } from "@/lib/noteColors"
import { Note, NoteColor } from "@/services/types"
import { pagesFromSelection } from "@/lib/textSelect"

interface TextViewerProps {
  docId: number
  /** Document.processing_status, so an empty page list mid-parse reads as
      "still processing" instead of "failed". */
  processingStatus?: string
}

// Statuses reachable before extraction has produced any ExtractedText rows.
const IN_PROGRESS_STATUSES = new Set(["pending", "parsing", "re-parsing"])

/**
 * Page/chapter reader for non-PDF formats (epub, docx, pptx, txt, md).
 *
 * These formats have no fixed visual layout to render - ExtractedText rows
 * are the source of truth (same rows chat/summaries/TTS already use), so
 * this renders that plain text directly instead of trying to reconstruct
 * page images the way PdfViewer does for actual PDFs.
 */
export default function TextViewer({ docId, processingStatus }: TextViewerProps) {
  const [pages, setPages] = useState<ExtractedPage[]>([])
  const [loading, setLoading] = useState(true)
  const [outlineOpen, setOutlineOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const suppressObserverRef = useRef(false)

  const {
    currentPage,
    setPage,
    setNumPages,
    pendingHighlight,
    clearPendingHighlight,
    tts,
    invertPdf,
    toggleInvertPdf,
  } = useReader()
  const loadNotes = useNotes((s) => s.load)
  const setNoteSelection = useNotes((s) => s.setSelection)
  const notes = useNotes((s) => (s.docId === docId ? s.notes : []))
  const colors = useNotes((s) => s.colors)

  useEffect(() => {
    void loadNotes(docId)
  }, [docId, loadNotes])

  const stillProcessing = pages.length === 0 && IN_PROGRESS_STATUSES.has(processingStatus || "")

  useEffect(() => {
    let mounted = true
    setLoading(true)
    getPages(docId)
      .then((data) => {
        if (!mounted) return
        setPages(data)
        setNumPages(data.length)
      })
      .catch((err) => console.error("Failed to load document pages:", err))
      .finally(() => mounted && setLoading(false))
    return () => {
      mounted = false
    }
    // Re-fetch once processing finishes (the dashboard already polls doc
    // status every 5s; this just reacts to it) so the reader doesn't stay
    // stuck showing "processing" after extraction actually completes.
  }, [docId, processingStatus, setNumPages])

  const scrollToPage = useCallback((pageNum: number) => {
    const el = pageRefs.current.get(pageNum)
    if (el) {
      suppressObserverRef.current = true
      el.scrollIntoView({ behavior: "smooth", block: "start" })
      setTimeout(() => (suppressObserverRef.current = false), 800)
    }
  }, [])

  const lastScrolledPage = useRef(currentPage)
  useEffect(() => {
    if (currentPage !== lastScrolledPage.current) {
      lastScrolledPage.current = currentPage
      scrollToPage(currentPage)
    }
  }, [currentPage, scrollToPage])

  // Citation jump from chat: no in-page text search here (no bounding boxes
  // for this format), so just land on the right page and clear the request.
  useEffect(() => {
    if (!pendingHighlight) return
    scrollToPage(pendingHighlight.page)
    const t = setTimeout(clearPendingHighlight, 900)
    return () => clearTimeout(t)
  }, [pendingHighlight, scrollToPage, clearPendingHighlight])

  useEffect(() => {
    const root = containerRef.current
    if (!root || pages.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (suppressObserverRef.current) return
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
        if (visible) {
          const pageNum = Number((visible.target as HTMLElement).dataset.page)
          if (pageNum) {
            lastScrolledPage.current = pageNum
            setPage(pageNum)
          }
        }
      },
      { root, threshold: [0.3, 0.5, 0.7] }
    )
    pageRefs.current.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [pages, setPage])

  const goToPage = (pageNum: number) => {
    if (pageNum < 1 || pageNum > pages.length) return
    setPage(pageNum)
    scrollToPage(pageNum)
  }

  const handleTextMouseUp = (pageNum: number) => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) return
    const text = sel.toString().replace(/\s+/g, " ").trim()
    if (text.length < 2) return
    const pages = pagesFromSelection(sel)
    const pageEl = pageRefs.current.get(pages?.start ?? pageNum)
    if (!pageEl) return
    if (!pages && (!sel.anchorNode || !pageEl.contains(sel.anchorNode))) return
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    setNoteSelection({
      docId,
      page: pages?.start ?? pageNum,
      endPage: pages && pages.end !== pages.start ? pages.end : undefined,
      quote: text,
      x: rect.left + rect.width / 2,
      y: rect.bottom,
    })
  }

  const chapters = pages
    .map((p) => ({
      page: p.page_number,
      title: Array.isArray(p.headings) ? p.headings[0]?.title : undefined,
    }))
    .filter((c) => c.title)

  return (
    <div className="relative flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-surface bg-panel px-3 py-1.5">
        <button
          type="button"
          onClick={() => setOutlineOpen((v) => !v)}
          title="Table of contents"
          className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
        >
          <List className="h-4 w-4" />
        </button>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className="rounded-md p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-xs text-cream-dim">
            {currentPage} / {pages.length}
          </span>
          <button
            type="button"
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= pages.length}
            className="rounded-md p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={toggleInvertPdf}
          title={invertPdf ? "Light background" : "Dark background"}
          className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
        >
          {invertPdf ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      {outlineOpen && (
        <div className="glass absolute left-2 top-12 z-20 max-h-[70vh] w-64 overflow-auto rounded-xl border border-surface p-2 shadow-glow">
          <div className="mb-1 flex items-center justify-between px-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
              Chapters
            </span>
            <button onClick={() => setOutlineOpen(false)} className="text-cream-dim hover:text-cream">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          {chapters.length === 0 ? (
            <p className="px-1 py-2 text-xs text-cream-dim">No chapter titles found</p>
          ) : (
            chapters.map((c) => (
              <button
                key={c.page}
                onClick={() => {
                  goToPage(c.page)
                  setOutlineOpen(false)
                }}
                className="block w-full truncate rounded-md px-2 py-1 text-left text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream"
              >
                {c.title}
              </button>
            ))
          )}
        </div>
      )}

      <div
        ref={containerRef}
        className={`min-h-0 flex-1 overflow-auto ${invertPdf ? "bg-[#1a1a1a]" : "bg-[#f5f0e8]"}`}
      >
        {loading ? (
          <div className="flex h-full w-full items-center justify-center">
            <LoadingSpinner />
          </div>
        ) : stillProcessing ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-cream-dim">
            <LoadingSpinner />
            Processing document…
          </div>
        ) : pages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-cream-dim">
            {processingStatus === "failed"
              ? "Processing this document failed."
              : "Failed to load document"}
          </div>
        ) : (
          <div className="mx-auto max-w-2xl px-8 py-10">
            {pages.map((page) => {
              const title = Array.isArray(page.headings) ? page.headings[0]?.title : undefined
              const isActivePage = tts.status !== "idle" && tts.page === page.page_number
              const pageNotes = notes.filter((n) => {
                const start = n.page_number
                const end = n.end_page ?? n.page_number
                return page.page_number >= start && page.page_number <= end
              })
              return (
                <div
                  key={page.page_number}
                  data-page={page.page_number}
                  ref={(el) => {
                    if (el) pageRefs.current.set(page.page_number, el)
                    else pageRefs.current.delete(page.page_number)
                  }}
                  id={`text-page-container-${page.page_number}`}
                  className={`mb-10 rounded-lg transition-colors ${
                    isActivePage ? "ring-2 ring-indigo/40" : ""
                  }`}
                >
                  {title && (
                    <h2
                      className={`mb-4 text-xl font-semibold ${
                        invertPdf ? "text-cream" : "text-[#2a2a2a]"
                      }`}
                    >
                      {title}
                    </h2>
                  )}
                  {page.content.split(/\n{2,}/).map((para, i) => (
                    <p
                      key={i}
                      onClick={() => {
                        if (window.getSelection()?.toString().trim()) return
                        void ttsEngine.readPage(docId, page.page_number, 0)
                      }}
                      onMouseUp={() => handleTextMouseUp(page.page_number)}
                      onTouchEnd={() => handleTextMouseUp(page.page_number)}
                      className={`mb-4 cursor-pointer whitespace-pre-line text-[15px] leading-relaxed transition-colors ${
                        invertPdf
                          ? "text-cream/90 hover:text-cream"
                          : "text-[#2a2a2a] hover:text-black"
                      }`}
                      title="Click to read this page aloud"
                    >
                      <HighlightedParagraph text={para} notes={pageNotes} colors={colors} />
                    </p>
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function HighlightedParagraph({
  text,
  notes,
  colors,
}: {
  text: string
  notes: Note[]
  colors: NoteColor[]
}) {
  const parts = splitHighlightedText(text, notes)
  if (parts.length === 1 && !parts[0].note) return <>{text}</>
  return (
    <>
      {parts.map((part, i) =>
        part.note ? (
          <mark
            key={i}
            className="rounded-[1px]"
            style={{
              background: hexToRgba(colorHex(colors, part.note.color), 0.28),
              borderBottom: `2px solid ${colorHex(colors, part.note.color)}`,
              color: "inherit",
            }}
          >
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        )
      )}
    </>
  )
}
