"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import TopBar from "@/components/layout/TopBar"
import Sidebar from "@/components/layout/Sidebar"
import UploadModal from "@/components/layout/UploadModal"
import SummaryPanel from "@/components/summary/SummaryPanel"
import AiPanel from "@/components/ai/AiPanel"
import PdfViewer, { clearPageTextCache } from "@/components/pdf/PdfViewer"
import TextViewer from "@/components/pdf/TextViewer"
import SelectionBar from "@/components/notes/SelectionBar"
import SelectionHandles from "@/components/notes/SelectionHandles"
import BottomBar from "@/components/tts/BottomBar"
import {
  getDocuments,
  deleteDocument,
  reparseDocument,
  Document,
  LayoutMode,
} from "@/services/api"
import { useReader } from "@/stores/reader"
import { useViewport } from "@/hooks/useViewport"
import { useAuth } from "@/stores/auth"
import PanelToggles from "@/components/layout/PanelToggles"
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts"
import { useReadingProgress } from "@/hooks/useReadingProgress"
import { ttsEngine } from "@/services/ttsEngine"
import { Plus, Minimize2, RefreshCw, BookOpen } from "lucide-react"

const LAYOUT_LABELS: Record<LayoutMode, string> = {
  auto: "Auto",
  research_paper: "Research paper",
  single_column: "Single column",
}

export default function DashboardPage() {
  const router = useRouter()
  const [documents, setDocuments] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [reparsing, setReparsing] = useState(false)
  const {
    selectedDocId,
    focusMode,
    summaryOpen,
    summaryWidth,
    setSummaryWidth,
    chatOpen,
    chatSide,
    aiPanelWidth,
    setAiPanelWidth,
    setUploadModalOpen,
  } = useReader()
  const user = useAuth((s) => s.user)

  const viewport = useViewport()

  useKeyboardShortcuts()
  useReadingProgress(selectedDocId)

  // Only PdfViewer performs a restore, so for every other format nothing
  // would ever unblock progress saving. Their reader has no page-level scroll
  // position to put back anyway, so there is nothing to wait for.
  const selectedFormat = documents.find((d) => d.id === selectedDocId)?.format
  useEffect(() => {
    if (selectedDocId != null && selectedFormat && selectedFormat !== "pdf") {
      useReader.getState().markProgressRestored(selectedDocId)
    }
  }, [selectedDocId, selectedFormat])

  const fetchDocs = useCallback(async () => {
    try {
      setDocuments(await getDocuments())
    } catch (error) {
      console.error("Failed to fetch documents:", error)
    } finally {
      setLoading(false)
    }
  }, [])

  // Only poll while something is still processing. A fixed 5s poll on a
  // quiet library re-rendered the whole dashboard (and fought the sidebar
  // order lock) forever for no user-visible gain. Upload / reparse paths
  // call fetchDocs themselves; focus/visibility catch external changes.
  const hasActiveJobs = documents.some(
    (d) => d.processing_status !== "completed" && d.processing_status !== "failed"
  )

  useEffect(() => {
    const token = localStorage.getItem("token")
    if (!token) {
      router.push("/login")
      return
    }
    fetchDocs()
  }, [router, fetchDocs])

  useEffect(() => {
    if (!hasActiveJobs) return
    const interval = setInterval(fetchDocs, 2000)
    return () => clearInterval(interval)
  }, [hasActiveJobs, fetchDocs])

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") fetchDocs()
    }
    document.addEventListener("visibilitychange", onVisible)
    window.addEventListener("focus", onVisible)
    return () => {
      document.removeEventListener("visibilitychange", onVisible)
      window.removeEventListener("focus", onVisible)
    }
  }, [fetchDocs])

  // Stop audio when switching documents
  useEffect(() => {
    ttsEngine.stop()
  }, [selectedDocId])

  const handleDelete = async (id: number) => {
    try {
      await deleteDocument(id)
      if (id === selectedDocId) useReader.getState().selectDoc(null)
      fetchDocs()
    } catch (error) {
      console.error("Failed to delete document:", error)
    }
  }

  const handleReparse = async (mode?: LayoutMode) => {
    if (!selectedDocId || reparsing) return
    setReparsing(true)
    try {
      clearPageTextCache(selectedDocId)
      await reparseDocument(selectedDocId, mode)
      fetchDocs()
    } catch (error) {
      console.error("Re-parse failed:", error)
    } finally {
      setReparsing(false)
    }
  }

  // Draggable divider between summary panel and PDF viewer
  const dragState = useRef<{ startX: number; startWidth: number } | null>(null)
  const onDividerDown = (e: React.PointerEvent) => {
    dragState.current = { startX: e.clientX, startWidth: summaryWidth }
    const onMove = (ev: PointerEvent) => {
      if (!dragState.current) return
      setSummaryWidth(dragState.current.startWidth + (ev.clientX - dragState.current.startX))
    }
    const onUp = () => {
      dragState.current = null
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
  }

  // AI panel divider: dragging away from the panel widens it, whichever
  // side it's docked on, so `invert` flips the delta sign for the left dock.
  const onAiDividerDown = (e: React.PointerEvent, invert: boolean) => {
    dragState.current = { startX: e.clientX, startWidth: aiPanelWidth }
    const onMove = (ev: PointerEvent) => {
      if (!dragState.current) return
      const delta = ev.clientX - dragState.current.startX
      setAiPanelWidth(dragState.current.startWidth + (invert ? -delta : delta))
    }
    const onUp = () => {
      dragState.current = null
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
  }

  const selectedDoc = documents.find((d) => d.id === selectedDocId)

  // Below the wide breakpoint the panels stop being columns. Three columns on
  // a 15" screen technically "fit" and the result is a page of text clipped on
  // both edges, which is the failure this whole branch exists to prevent: the
  // reader keeps the full width and a panel is drawn over it instead.
  const overlayPanels = viewport !== "wide"
  const anyPanelOpen = summaryOpen || chatOpen
  const closePanels = useReader((s) => s.closePanels)
  const setPanelsExclusive = useReader((s) => s.setPanelsExclusive)

  useEffect(() => {
    setPanelsExclusive(overlayPanels)
  }, [overlayPanels, setPanelsExclusive])

  const panelClass = (side: "left" | "right") => {
    if (!overlayPanels) {
      return `shrink-0 overflow-hidden bg-panel ${
        side === "left" ? "border-r" : "border-l"
      } border-surface`
    }
    // A phone gets the full width - a 320px drawer beside 55px of dead space
    // helps nobody. Larger overlays stay narrow enough to keep the page
    // visible behind them, which is what makes "peek at the outline and come
    // back" feel like one motion instead of a context switch.
    return [
      "absolute inset-y-0 z-40 overflow-hidden bg-panel shadow-glow-strong",
      side === "left" ? "left-0 border-r" : "right-0 border-l",
      "border-surface",
      "w-full sm:w-[22rem] md:w-[24rem]",
    ].join(" ")
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-void text-cream">
      {!focusMode && <TopBar />}
      {focusMode && (
        <button
          onClick={() => useReader.getState().toggleFocusMode()}
          title="Exit focus mode (Esc)"
          className="glass fixed right-4 top-4 z-40 flex items-center gap-1.5 rounded-full border border-surface px-3 py-1.5 text-xs font-medium text-cream opacity-40 shadow-glow transition-opacity hover:opacity-100"
        >
          <Minimize2 className="h-3.5 w-3.5" />
          Exit focus
        </button>
      )}

      <div className="relative flex min-h-0 flex-1">
        {!focusMode && (
          <Sidebar documents={documents} loading={loading} onDelete={handleDelete} user={user} />
        )}

        {/* One scrim for whichever panel is overlaying the reader. Tapping it
            dismisses - the gesture people already expect from a drawer, and
            the only dismissal that is reachable one-handed on a phone. The
            scrim is skipped when no document is selected because the panels
            don't render in that state - a scrim with nothing behind it reads
            as a broken screen. */}
        {overlayPanels && anyPanelOpen && selectedDoc && !focusMode && (
          <div
            onClick={closePanels}
            aria-hidden
            className="absolute inset-0 z-30 bg-black/60 backdrop-blur-[2px]"
          />
        )}

        {selectedDoc ? (
          <div className="flex min-w-0 flex-1">
            {!focusMode && chatOpen && chatSide === "left" && (
              <>
                <div
                  style={overlayPanels ? undefined : { width: aiPanelWidth }}
                  className={panelClass("left")}
                >
                  <AiPanel docId={selectedDoc.id} docTitle={selectedDoc.title} />
                </div>
                {!overlayPanels && (
                  <div
                    onPointerDown={(e) => onAiDividerDown(e, false)}
                    title="Drag to resize"
                    className="w-1 shrink-0 cursor-col-resize bg-surface transition-colors hover:bg-indigo/50"
                  />
                )}
              </>
            )}

            {!focusMode && summaryOpen && (
              <>
                <div
                  style={overlayPanels ? undefined : { width: summaryWidth }}
                  className={panelClass("left")}
                >
                  <SummaryPanel docId={selectedDoc.id} />
                </div>
                {!overlayPanels && (
                  <div
                    onPointerDown={onDividerDown}
                    title="Drag to resize"
                    className="w-1 shrink-0 cursor-col-resize bg-surface transition-colors hover:bg-indigo/50"
                  />
                )}
              </>
            )}

            <div className="flex min-w-0 flex-1 flex-col">
              {!focusMode && selectedDoc.format === "pdf" && (
                <div className="reader-scroll flex shrink-0 items-center gap-2 overflow-x-auto border-b border-surface bg-panel px-3 py-1.5">
                  <span className="hidden shrink-0 text-[10px] font-semibold uppercase tracking-wide text-cream-dim sm:inline">
                    Layout
                  </span>
                  <select
                    value={selectedDoc.layout_mode || "auto"}
                    disabled={reparsing}
                    onChange={(e) => handleReparse(e.target.value as LayoutMode)}
                    className="rounded-md border border-surface bg-void px-2 py-1 text-[11px] text-cream focus:outline-none disabled:opacity-50"
                    title="Change layout mode and re-parse the PDF"
                  >
                    {(Object.keys(LAYOUT_LABELS) as LayoutMode[]).map((m) => (
                      <option key={m} value={m}>
                        {LAYOUT_LABELS[m]}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={reparsing}
                    onClick={() => handleReparse()}
                    title="Re-parse PDF with current layout mode"
                    className="flex items-center gap-1 rounded-md border border-surface px-2 py-1 text-[11px] text-cream-dim transition-colors hover:bg-surface hover:text-cream disabled:opacity-50"
                  >
                    <RefreshCw className={`h-3 w-3 ${reparsing ? "animate-spin" : ""}`} />
                    {reparsing ? "Re-parsing…" : "Re-parse"}
                  </button>
                  {(selectedDoc.processing_status === "re-parsing" ||
                    selectedDoc.processing_status === "parsing") && (
                    <span className="text-[10px] text-indigo animate-pulse">
                      extracting text…
                    </span>
                  )}
                </div>
              )}
              <div className="min-h-0 min-w-0 flex-1">
                {selectedDoc.format === "pdf" ? (
                  <PdfViewer docId={selectedDoc.id} />
                ) : (
                  <TextViewer
                    docId={selectedDoc.id}
                    processingStatus={selectedDoc.processing_status}
                  />
                )}
              </div>
            </div>

            {!focusMode && chatOpen && chatSide === "right" && (
              <>
                {!overlayPanels && (
                  <div
                    onPointerDown={(e) => onAiDividerDown(e, true)}
                    title="Drag to resize"
                    className="w-1 shrink-0 cursor-col-resize bg-surface transition-colors hover:bg-indigo/50"
                  />
                )}
                <div
                  style={overlayPanels ? undefined : { width: aiPanelWidth }}
                  className={panelClass("right")}
                >
                  <AiPanel docId={selectedDoc.id} docTitle={selectedDoc.title} />
                </div>
              </>
            )}
          </div>
        ) : (
          // Two different states share this slot: a genuinely empty library,
          // and a library with documents where none is open yet. Showing the
          // first-run copy in the second case contradicts the sidebar sitting
          // right next to it, already listing the user's documents.
          <div className="flex flex-1 flex-col items-center justify-center gap-4">
            {documents.length === 0 ? (
              <>
                <button
                  onClick={() => setUploadModalOpen(true)}
                  className="gradient-primary flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.02]"
                >
                  <Plus className="h-4 w-4" />
                  Add your first document
                </button>
                <p className="text-sm text-cream-dim">Your documents will appear here</p>
              </>
            ) : (
              <>
                <BookOpen className="h-8 w-8 text-cream-dim" />
                <p className="text-sm text-cream-dim">
                  Select a document to start reading
                </p>
              </>
            )}
          </div>
        )}
      </div>

      <BottomBar />
      {/* Phone-only panel switcher. It lives at the bottom because that is the
          part of a phone screen a thumb reaches; the same controls sit in the
          top bar on a desktop, where a pointer makes reach irrelevant. */}
      {!focusMode && viewport === "phone" && (
        <nav className="shrink-0 border-t border-surface bg-panel pb-[env(safe-area-inset-bottom)]">
          <PanelToggles size="lg" />
        </nav>
      )}
      <SelectionBar />
      <SelectionHandles />
      <UploadModal onUploaded={fetchDocs} />
    </div>
  )
}
