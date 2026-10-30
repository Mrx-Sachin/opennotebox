"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
  getDocument,
  deleteDocument,
  reparseDocument,
  LayoutMode,
} from "@/services/api"
import { useReader } from "@/stores/reader"
import { useViewport } from "@/hooks/useViewport"
import { useAuth } from "@/stores/auth"
import PanelToggles from "@/components/layout/PanelToggles"
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts"
import { useReadingProgress } from "@/hooks/useReadingProgress"
import { ttsEngine } from "@/services/ttsEngine"
import { Minimize2, RefreshCw, Loader2, AlertCircle, UsersRound } from "lucide-react"
import Link from "next/link"
import { useDocuments, isDocumentProcessing } from "@/hooks/useDocuments"
import ShareDocumentDialog from "@/components/library/ShareDocumentDialog"
import type { SharedDocumentUpdate } from "@/services/types"

const LAYOUT_LABELS: Record<LayoutMode, string> = {
  auto: "Auto",
  research_paper: "Research paper",
  single_column: "Single column",
}

export default function ReaderPage() {
  const router = useRouter()
  const params = useParams<{ docId: string }>()
  const routeDocId = Number(params.docId)
  const queryClient = useQueryClient()
  const [reparsing, setReparsing] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
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
  } = useReader()
  const user = useAuth((s) => s.user)
  const documentQuery = useQuery({
    queryKey: ["document", user?.id, routeDocId],
    queryFn: () => getDocument(routeDocId),
    enabled: user != null && Number.isSafeInteger(routeDocId) && routeDocId > 0,
    retry: false,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => query.state.data && isDocumentProcessing(query.state.data) ? 2000 : false,
  })
  const selectedDoc = documentQuery.data
  const notebookId = selectedDoc?.notebook_id ?? null
  const documentsQuery = useDocuments(user?.id, notebookId, Boolean(selectedDoc))
  const documents = documentsQuery.data ?? []
  const loading = documentsQuery.isPending
  const libraryHref = notebookId ? `/dashboard?notebook=${notebookId}` : "/dashboard?unfiled=true"

  useEffect(() => {
    if (selectedDoc && useReader.getState().selectedDocId !== selectedDoc.id) {
      useReader.getState().selectDoc(selectedDoc.id, selectedDoc.num_pages, selectedDoc.last_read_page ?? undefined)
    }
  }, [selectedDoc])

  const viewport = useViewport()

  useKeyboardShortcuts()
  useReadingProgress()

  // Only PdfViewer performs a restore, so for every other format nothing
  // would ever unblock progress saving. Their reader has no page-level scroll
  // position to put back anyway, so there is nothing to wait for.
  const selectedFormat = selectedDoc?.format
  useEffect(() => {
    if (selectedDocId != null && selectedFormat && selectedFormat !== "pdf") {
      useReader.getState().markProgressRestored(selectedDocId)
    }
  }, [selectedDocId, selectedFormat])

  const fetchDocs = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
      queryClient.invalidateQueries({ queryKey: ["document"] }),
      queryClient.invalidateQueries({ queryKey: ["library"] }),
    ])
  }, [queryClient])

  useEffect(() => {
    const state = useReader.getState()
    if (!state.activePodcast && !state.activeReading) {
      ttsEngine.stop()
    }
  }, [selectedDocId])

  const handleDelete = async (id: number) => {
    try {
      await deleteDocument(id)
      if (useReader.getState().activeReading?.docId === id) ttsEngine.stop()
      if (id === selectedDocId) {
        useReader.getState().selectDoc(null)
        router.replace(libraryHref)
      }
      fetchDocs()
    } catch (error) {
      console.error("Failed to delete document:", error)
    }
  }

  const [viewerMountKey, setViewerMountKey] = useState(0)
  const processingRunRef = useRef<{ docId: number; wasProcessing: boolean } | null>(null)

  useEffect(() => {
    if (!selectedDoc) return
    const prev = processingRunRef.current
    const isProcessing = isDocumentProcessing(selectedDoc)
    if (prev && prev.docId === selectedDoc.id) {
      if (
        prev.wasProcessing &&
        !isProcessing &&
        (selectedDoc.processing_status === "extracted" || selectedDoc.processing_status === "completed")
      ) {
        ttsEngine.clearPageSentencesCache(selectedDoc.id)
        clearPageTextCache(selectedDoc.id)
        setViewerMountKey((key) => key + 1)
      }
    }
    processingRunRef.current = { docId: selectedDoc.id, wasProcessing: isProcessing }
  }, [selectedDoc])

  const handleReparse = async (mode?: LayoutMode) => {
    if (!selectedDocId || reparsing || selectedDoc?.read_only) return
    setReparsing(true)
    try {
      ttsEngine.clearPageSentencesCache(selectedDocId)
      clearPageTextCache(selectedDocId)
      await reparseDocument(selectedDocId, mode)
      await fetchDocs()
      ttsEngine.clearPageSentencesCache(selectedDocId)
      clearPageTextCache(selectedDocId)
      if (useReader.getState().selectedDocId === selectedDocId) {
        setViewerMountKey((key) => key + 1)
      }
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
    <div className="flex h-screen flex-col overflow-hidden bg-void text-cream" style={{ paddingBottom: "var(--podcast-player-height, 0px)" }}>
      {!focusMode && <TopBar libraryHref={libraryHref} libraryLabel={notebookId ? "Notebook" : "Unfiled"} />}
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
          <Sidebar documents={selectedDoc && !documents.some((document) => document.id === selectedDoc.id) ? [selectedDoc, ...documents] : documents} loading={loading} onDelete={handleDelete} user={user} />
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
              {!focusMode && selectedDoc.can_share && (
                <div className="flex shrink-0 items-center justify-between gap-3 border-b border-surface bg-panel px-3 py-2">
                  <span className="min-w-0 truncate text-xs text-cream-dim">
                    {selectedDoc.is_shared ? "Listed in the team library" : "Private document"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setShareOpen(true)}
                    className="flex shrink-0 items-center gap-1.5 rounded-md border border-surface px-2.5 py-1.5 text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo"
                  >
                    <UsersRound size={14} aria-hidden="true" />
                    {selectedDoc.is_shared ? "Manage sharing" : "Share with team"}
                  </button>
                </div>
              )}
              {!focusMode && selectedDoc.format === "pdf" && (
                <div className="reader-scroll flex shrink-0 items-center gap-2 overflow-x-auto border-b border-surface bg-panel px-3 py-1.5">
                  <span className="hidden shrink-0 text-[10px] font-semibold uppercase tracking-wide text-cream-dim sm:inline">
                    Layout
                  </span>
                  <select
                    value={selectedDoc.layout_mode || "auto"}
                    disabled={reparsing || Boolean(selectedDoc.read_only)}
                    onChange={(e) => handleReparse(e.target.value as LayoutMode)}
                    className="rounded-md border border-surface bg-void px-2 py-1 text-[11px] text-cream focus:outline-none disabled:opacity-50"
                    title={selectedDoc.read_only ? "This shared document cannot be re-parsed" : "Change layout mode and re-parse the PDF"}
                  >
                    {(Object.keys(LAYOUT_LABELS) as LayoutMode[]).map((m) => (
                      <option key={m} value={m}>
                        {LAYOUT_LABELS[m]}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={reparsing || Boolean(selectedDoc.read_only)}
                    onClick={() => handleReparse()}
                    title={selectedDoc.read_only ? "This shared document cannot be re-parsed" : "Re-parse PDF with current layout mode"}
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
                  <PdfViewer key={`${selectedDoc.id}-${viewerMountKey}`} docId={selectedDoc.id} />
                ) : (
                  <TextViewer key={selectedDoc.id}
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
          <div className="flex flex-1 flex-col items-center justify-center gap-4">
            {documentQuery.isPending && Number.isSafeInteger(routeDocId) && routeDocId > 0 ? (
              <><Loader2 className="h-7 w-7 animate-spin text-indigo" /><p className="text-sm text-cream-dim">Opening document</p></>
            ) : (
              <><AlertCircle className="h-7 w-7 text-cream-dim" /><p className="text-sm text-cream-dim">This document is unavailable.</p><Link href="/dashboard" className="rounded-lg bg-surface px-4 py-2 text-sm text-cream">Back to library</Link></>
            )}
          </div>
        )}
      </div>

      <BottomBar />
      {!focusMode && viewport === "phone" && (
        <nav className="shrink-0 border-t border-surface bg-panel pb-[env(safe-area-inset-bottom)]">
          <PanelToggles size="lg" />
        </nav>
      )}
      <SelectionBar />
      <SelectionHandles />
      <UploadModal onUploaded={fetchDocs} notebookId={notebookId} />
      {selectedDoc?.can_share && shareOpen && <ShareDocumentDialog
        document={selectedDoc}
        onClose={() => setShareOpen(false)}
        onUpdated={(update: SharedDocumentUpdate) => {
          const nextDocument = {
            ...selectedDoc,
            is_shared: update.is_shared,
            ...(update.title !== undefined ? { shared_title: update.title } : {}),
            ...(update.author !== undefined ? { author: update.author } : {}),
            ...(update.description !== undefined ? { description: update.description } : {}),
            ...(update.topics !== undefined ? { topics: update.topics } : {}),
            ...(update.document_type !== undefined ? { document_type: update.document_type } : {}),
            ...(update.language !== undefined ? { language: update.language } : {}),
          }
          queryClient.setQueryData(["document", user?.id, selectedDoc.id], nextDocument)
          void queryClient.invalidateQueries({ queryKey: ["documents"] })
          void queryClient.invalidateQueries({ queryKey: ["library"] })
          setShareOpen(false)
        }}
      />}
    </div>
  )
}
