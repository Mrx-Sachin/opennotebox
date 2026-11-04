"use client"

import axios from "axios"

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useViewport } from "@/hooks/useViewport"
import { useDocumentLayout } from "@/hooks/useDocumentLayout"
import { getFileUrl, getPage } from "@/services/api"
import { useReader } from "@/stores/reader"
import LoadingSpinner from "@/components/ui/LoadingSpinner"
import { ttsEngine } from "@/services/ttsEngine"
import { Sentence } from "@/lib/sentences"
import {
  dehyphenateForSpeech,
  buildPageSentences,
  isPointInRejectedDoclingRegion,
} from "@/lib/narrationFilter"
import {
  DoclingRegion,
  extractDoclingRegions,
} from "@/services/types"
import {
  normalizeWithMap,
  findRange,
  findRangeProgressive,
  findCitationEvidenceRange,
  NormalizedText,
} from "@/lib/textAlign"
import { copyText } from "@/lib/clipboard"
import { useNotes } from "@/stores/notes"
import { applyNoteHighlights } from "@/lib/noteHighlights"
import { hitTestLink, linkHoverTitle, pdfClickIntent, PdfLinkRect } from "@/lib/pdfLinks"
import {
  selectWordAtPoint,
  selectionQuote,
  leafTextSpans,
  disableNonReadingHits,
  ensureEndOfContent,
  isReadingSpan,
  setSelectionFromPoints,
  pagesFromSelection,
  textLayerOf,
  extendSelectionEdge,
} from "@/lib/textSelect"
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  List,
  ChevronDown,
  Moon,
  Sun,
  LocateFixed,
  X,
  TextSelect,
  Volume2,
  Layers,
} from "lucide-react"

interface PdfViewerProps {
  docId: number
  pageTextVersion?: number
  layoutResetting?: boolean
}

interface OutlineItem {
  title: string
  pageNumber?: number
  items: OutlineItem[]
}

/** Extracted page text + derived sentence/normalization data, cached per doc+page. */
interface PageText {
  content: string
  sentences: Sentence[]
  normContent: NormalizedText
  doclingRegions: DoclingRegion[]
  hasDocling: boolean
}

// Canvas backing-store multiplier. Above 1 the page is drawn at more device
// pixels than CSS pixels, which is what keeps small type sharp; it must never
// leak into layout (see ensureRendered).
const SUPERSAMPLE = 1.5

// Keep the cache bounded: a 1000-page book with all pages open would
// otherwise accumulate ~1000 entries (one per page of raw content text).
const MAX_PAGE_TEXT_ENTRIES = 64

const pageTextCache = new Map<string, Promise<PageText>>()

// Monotonic version per document: incremented on clear so in-flight fetches
// do not re-cache stale results (e.g., after a re-parse the old page text
// would silently overwrite the cache when its pending promise resolved).
const pageTextCacheVersion = new Map<number, number>()

/** Drop cached page text for a document (call after re-parse). */
export function clearPageTextCache(docId?: number) {
  if (docId == null) {
    pageTextCache.clear()
    pageTextCacheVersion.clear()
    return
  }
  const prefix = `${docId}:`
  for (const key of Array.from(pageTextCache.keys())) {
    if (key.startsWith(prefix)) pageTextCache.delete(key)
  }
  pageTextCacheVersion.set(docId, (pageTextCacheVersion.get(docId) || 0) + 1)
}

function loadPageText(docId: number, pageNum: number): Promise<PageText> {
  const key = `${docId}:${pageNum}`
  const version = pageTextCacheVersion.get(docId) || 0
  let cached = pageTextCache.get(key)
  if (!cached) {
    cached = getPage(docId, pageNum)
      .then((page) => {
        const content = page.content || ""
        const boxes = page.bounding_boxes ?? null
        const regions = page.layout_regions ?? null
        const exclusions = page.narration_exclusions ?? null
        const doclingRegions = extractDoclingRegions(regions ?? boxes)
        const hasDocling = doclingRegions.length > 0
        return {
          content,
          sentences: buildPageSentences(content, exclusions),
          normContent: normalizeWithMap(content),
          doclingRegions,
          hasDocling,
        }
      })
      .catch((err) => {
        // Don't cache rejected promises forever - a transient network error
        // should not make a page permanently unreadable.
        if (pageTextCache.get(key) === cached) pageTextCache.delete(key)
        throw err
      })
    // Evict oldest entries when the cache grows beyond the cap so a long
    // reading session never accumulates gigabytes of stale page content.
    if (pageTextCache.size >= MAX_PAGE_TEXT_ENTRIES) {
      const oldestKey = pageTextCache.keys().next().value
      if (oldestKey !== undefined) pageTextCache.delete(oldestKey)
    }
    pageTextCache.set(key, cached)
  }
  return cached.then((result) => {
    // If the cache was cleared while this fetch was in flight (e.g., after
    // a re-parse), discard the stale result rather than re-caching it.
    if ((pageTextCacheVersion.get(docId) || 0) > version) {
      return loadPageText(docId, pageNum)
    }
    return result
  })
}

/** Flatten PDF.js outline entries into titles with resolved page numbers.
 * Module scope, not a component closure: it is recursive and depends on
 * nothing but its arguments, so keeping it inside the component only made
 * it a new function identity on every render. */
async function buildOutlineFn (pdf: any, items: any[]): Promise<OutlineItem[]> {
  const result: OutlineItem[] = []
  for (const item of items) {
    let pageNumber: number | undefined = undefined
    if (item.dest) {
      try {
        let dest = item.dest
        if (typeof dest === "string") dest = await pdf.getDestination(dest)
        if (Array.isArray(dest) && dest.length > 0) {
          pageNumber = (await pdf.getPageIndex(dest[0])) + 1
        }
      } catch {
        /* outline entry without resolvable destination */
      }
    }
    result.push({
      title: item.title,
      pageNumber,
      items: item.items?.length ? await buildOutlineFn(pdf, item.items) : [],
    })
  }
  return result
}

/** How many pages above/below the viewport stay mounted as real PdfPage
 * components. Everything else is a height-only placeholder. Without this a
 * 600-page book mounts 600 React trees (effects, observers, store subs) and
 * every library click / panel toggle pays that tax before paint. */
const PAGE_MOUNT_BUFFER = 5
/** Vertical gap between pages - matches `gap-6` (1.5rem) on the scroll stack.
 * Must stay in lockstep with the scroll container's `gap-*` class; a mismatch
 * makes zoom/page-step math drift so the viewport jumps to the wrong page. */
const PAGE_STACK_GAP_PX = 24

export default function PdfViewer({ docId, pageTextVersion = 0, layoutResetting = false }: PdfViewerProps) {
  const layout = useDocumentLayout(docId)
  const [layoutVersion, setLayoutVersion] = useState(0)
  useEffect(() => {
    if (layout.status !== "ready") return
    clearPageTextCache(docId)
    ttsEngine.clearPageSentencesCache(docId, true)
    setLayoutVersion((version) => version + 1)
  }, [docId, layout.status])
  const { currentPage, setPage, numPages, setNumPages } = useReader()
  const invertPdf = useReader((s) => s.invertPdf)
  const toggleInvertPdf = useReader((s) => s.toggleInvertPdf)
  const followReading = useReader((s) => s.followReading)
  const setFollowReading = useReader((s) => s.setFollowReading)
  const citationToast = useReader((s) => s.citationToast)
  const clearCitationToast = useReader((s) => s.clearCitationToast)
  const pendingHighlightPage = useReader((s) => s.pendingHighlight?.docId === docId ? s.pendingHighlight.page : null)
  const ttsStatus = useReader((s) => s.tts.docId === docId ? s.tts.status : "idle")
  const ttsPage = useReader((s) => s.tts.docId === docId ? s.tts.page : null)
  const loadNotes = useNotes((s) => s.load)

  useEffect(() => {
    void loadNotes(docId)
  }, [docId, loadNotes])
  // null means "fit the page to the column", recomputed whenever the column
  // resizes. A fixed 1.0 was the reason a page was clipped on both edges the
  // moment two side panels were open: a US Letter page is 612pt wide, so at
  // scale 1 it needs 612px and simply overflowed whatever was left. Fit is
  // also the only sane default on a phone, where no fixed scale works.
  const [zoomOverride, setZoomOverride] = useState<number | null>(null)
  const [fitScale, setFitScale] = useState(1.0)
  const scale = zoomOverride ?? fitScale
  const [pageBase, setPageBase] = useState<{ width: number; height: number } | null>(null)
  const pageBaseWidth = pageBase?.width ?? null
  const [loading, setLoading] = useState(true)
  const [pdfDoc, setPdfDoc] = useState<any>(null)
  const pdfDocRef = useRef<any>(null)
  const [outline, setOutline] = useState<OutlineItem[]>([])
  const [showOutline, setShowOutline] = useState(false)
  // Select mode: clicks never start TTS, so a drag can start on the word
  // itself. Off by default; double-click / long-press still select a word.
  const [selectMode, setSelectMode] = useState(false)
  const [showOverlay, setShowOverlay] = useState(false)
  const viewport = useViewport()
  const touchSelect = viewport === "phone"
  // Inclusive range of pages that currently have a live PdfPage instance.
  const [mountRange, setMountRange] = useState({ lo: 1, hi: PAGE_MOUNT_BUFFER * 2 + 1 })
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const suppressObserverRef = useRef(false)
  // Restored in useLayoutEffect after a zoom so the same page+offset stays
  // on screen. Pixel scrollTop is wrong the moment page height changes.
  const pendingZoomScrollRef = useRef<{ pageIndex: number; offsetRatio: number } | null>(null)

  // Reading in progress + the user scrolled themselves: stop following.
  // wheel/touch only fire for user-initiated scrolling, so programmatic
  // scrollIntoView calls never trip this.
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    const pause = () => {
      const { tts, followReading } = useReader.getState()
      if (tts.docId === docId && followReading && (tts.status === "playing" || tts.status === "preparing")) {
        setFollowReading(false)
      }
    }
    container.addEventListener("wheel", pause, { passive: true })
    container.addEventListener("touchmove", pause, { passive: true })
    return () => {
      container.removeEventListener("wheel", pause)
      container.removeEventListener("touchmove", pause)
    }
  }, [docId, setFollowReading])

  const returnToReading = useCallback(() => {
    setFollowReading(true)
    const active = document.querySelector(".tts-sentence-active, .tts-sentence-preparing")
    if (active) {
      active.scrollIntoView({ behavior: "smooth", block: "center" })
    } else if (ttsPage) {
      const el = document.getElementById(`pdf-page-container-${ttsPage}`)
      el?.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }, [setFollowReading, ttsPage])

  useEffect(() => {
    let mounted = true
    const loadPdf = async () => {
      setLoading(true)
      setPdfDoc(null)
      setOutline([])
      try {
        const pdfjs = await import("pdfjs-dist")
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs"
        const token = localStorage.getItem("token")
        const pdf = await pdfjs.getDocument({
          url: getFileUrl(docId),
          httpHeaders: token ? { Authorization: `Bearer ${token}` } : undefined,
        }).promise
        if (!mounted) {
          pdf.destroy()
          return
        }
        pdfDocRef.current?.destroy()
        pdfDocRef.current = pdf
        setPdfDoc(pdf)
        setNumPages(pdf.numPages)
        // Page 1's unscaled width is what "fit" is measured against. Taken
        // from the document rather than assumed, because A4, US Letter and
        // the odd cropped scan are all different and guessing one of them
        // clips the others.
        try {
          const first = await pdf.getPage(1)
          const v = first.getViewport({ scale: 1 })
          if (mounted) setPageBase({ width: v.width, height: v.height })
        } catch {
          /* fall back to the previous fit */
        }
        try {
          const rawOutline = await pdf.getOutline()
          if (rawOutline && mounted) setOutline(await buildOutlineFn(pdf, rawOutline))
        } catch {
          /* no outline */
        }
      } catch (error) {
        console.error("Failed to load PDF:", error)
      } finally {
        if (mounted) setLoading(false)
      }
    }
    loadPdf()
    return () => {
      mounted = false
      pdfDocRef.current?.destroy()
      pdfDocRef.current = null
    }
  }, [docId, setNumPages])

  // Recompute fit whenever the reader column changes size. A ResizeObserver
  // rather than a window resize listener because most of the size changes here
  // are not window resizes at all - opening a panel, dragging a divider,
  // leaving focus mode - and those are exactly the cases that used to clip.
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container || !pageBaseWidth) return

    const recompute = () => {
      // Read the real padding rather than hard-coding it to match a class
      // name: the padding is responsive, and a constant that silently stops
      // matching produces a page a few pixels too wide, which is a horizontal
      // scrollbar on every page of the document.
      const cs = getComputedStyle(container)
      const padding = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)
      const BORDER = 2 // the page wrapper's 1px border, both sides
      const available = container.clientWidth - padding - BORDER
      if (available <= 0) return
      // Never blow a small page up past its natural size: a half-width scan
      // stretched to a 1600px column is unreadably soft.
      setFitScale(Math.min(available / pageBaseWidth, 1.6))
    }

    recompute()
    const ro = new ResizeObserver(recompute)
    ro.observe(container)
    return () => ro.disconnect()
  }, [pageBaseWidth])

  const totalPages = pdfDoc?.numPages ?? numPages ?? 0

  const expandMountAround = useCallback(
    (pageNum: number, pageCount: number) => {
      if (pageCount < 1) return
      const lo = Math.max(1, pageNum - PAGE_MOUNT_BUFFER)
      const hi = Math.min(pageCount, pageNum + PAGE_MOUNT_BUFFER)
      setMountRange((prev) => (prev.lo === lo && prev.hi === hi ? prev : { lo, hi }))
    },
    []
  )

  /** Keep only pages near the viewport (and TTS/citation targets) mounted. */
  const syncMountRangeFromScroll = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container || !pageBase || totalPages < 1) return
    const pageStep = pageBase.height * scale + PAGE_STACK_GAP_PX
    if (pageStep <= 0) return
    const first = Math.floor(container.scrollTop / pageStep) + 1
    const last = Math.ceil((container.scrollTop + container.clientHeight) / pageStep)
    let lo = Math.max(1, first - PAGE_MOUNT_BUFFER)
    let hi = Math.min(totalPages, Math.max(first, last) + PAGE_MOUNT_BUFFER)
    for (const p of [currentPage, ttsPage, pendingHighlightPage]) {
      if (typeof p === "number" && p >= 1) {
        lo = Math.min(lo, Math.max(1, p - 1))
        hi = Math.max(hi, Math.min(totalPages, p + 1))
      }
    }
    setMountRange((prev) => (prev.lo === lo && prev.hi === hi ? prev : { lo, hi }))
  }, [pageBase, scale, totalPages, currentPage, ttsPage, pendingHighlightPage])

  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        syncMountRangeFromScroll()
      })
    }
    container.addEventListener("scroll", onScroll, { passive: true })
    syncMountRangeFromScroll()
    return () => {
      container.removeEventListener("scroll", onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [syncMountRangeFromScroll, pdfDoc])

  // New document: start the mount window at page 1 until restore/scroll runs.
  useEffect(() => {
    setMountRange({ lo: 1, hi: Math.min(totalPages || PAGE_MOUNT_BUFFER * 2 + 1, PAGE_MOUNT_BUFFER * 2 + 1) })
  }, [docId]) // eslint-disable-line react-hooks/exhaustive-deps -- reset only on doc change

  const scrollToPage = useCallback(
    (pageNum: number) => {
      const pageCount = totalPages || pageNum
      // Mount the target before scrolling - placeholders cannot paint text
      // layers or citation flashes, and getElementById still finds them so a
      // scroll-without-mount would land on an empty box.
      expandMountAround(pageNum, pageCount)
      suppressObserverRef.current = true
      const run = () => {
        const element = document.getElementById(`pdf-page-container-${pageNum}`)
        if (element) {
          element.scrollIntoView({ behavior: "smooth", block: "start" })
        }
        setTimeout(() => {
          suppressObserverRef.current = false
          syncMountRangeFromScroll()
        }, 800)
      }
      // Two frames: one for setState commit, one for PdfPage layout.
      requestAnimationFrame(() => requestAnimationFrame(run))
    },
    [totalPages, expandMountAround, syncMountRangeFromScroll]
  )

  const pageList = useMemo(() => {
    if (!pdfDoc) return [] as number[]
    return Array.from({ length: pdfDoc.numPages }, (_, i) => i + 1)
  }, [pdfDoc])

  const fallbackSize = useMemo(
    () =>
      pageBase
        ? { width: pageBase.width * scale, height: pageBase.height * scale }
        : null,
    [pageBase, scale]
  )

  const applyZoom = useCallback(
    (nextOverride: number | null) => {
      const container = scrollContainerRef.current
      const oldScale = scale
      const newScale = nextOverride ?? fitScale
      if (container && pageBase && oldScale > 0 && newScale > 0 && oldScale !== newScale) {
        const oldPageH = pageBase.height * oldScale
        const oldStep = oldPageH + PAGE_STACK_GAP_PX
        const pageIndex = Math.floor(container.scrollTop / Math.max(oldStep, 1))
        const offset = container.scrollTop - pageIndex * oldStep
        pendingZoomScrollRef.current = {
          pageIndex,
          offsetRatio: oldPageH > 0 ? offset / oldPageH : 0,
        }
        suppressObserverRef.current = true
      }
      setZoomOverride(nextOverride)
    },
    [scale, fitScale, pageBase]
  )

  useLayoutEffect(() => {
    const pending = pendingZoomScrollRef.current
    const container = scrollContainerRef.current
    if (!pending || !container || !pageBase) return
    pendingZoomScrollRef.current = null
    const pageH = pageBase.height * scale
    const step = pageH + PAGE_STACK_GAP_PX
    container.scrollTop = pending.pageIndex * step + pending.offsetRatio * pageH
    suppressObserverRef.current = false
    syncMountRangeFromScroll()
  }, [scale, pageBase, syncMountRangeFromScroll])

  // External page changes (chat citations, bottom bar, bookmarks, TTS advance)
  const lastScrolledPage = useRef(currentPage)
  useEffect(() => {
    if (currentPage !== lastScrolledPage.current) {
      lastScrolledPage.current = currentPage
      scrollToPage(currentPage)
    }
  }, [currentPage, scrollToPage])

  // Restoring a reading position is not a page *change* - the store already
  // holds the saved page before this component mounts - so the effect above
  // never fires for it and the reader silently opened at page 1. This is the
  // one-shot that actually performs the jump, and it waits for pageBase so
  // the placeholder heights are right; scrolling before then lands short by
  // the accumulated difference between the estimate and the real page size.
  const restoredForDoc = useRef<number | null>(null)
  const progressRestoredFor = useReader((state) => state.progressRestoredFor)
  useEffect(() => {
    if (!pdfDoc || !pageBase) return
    if (restoredForDoc.current === docId && progressRestoredFor === docId) return
    if (useReader.getState().selectedDocId !== docId) return
    const pending = useReader.getState().pendingHighlight
    const target = pending?.docId === docId ? pending.page : useReader.getState().currentPage
    if (target <= 1) {
      restoredForDoc.current = docId
      useReader.getState().markProgressRestored(docId)
      return
    }
    const t = setTimeout(() => {
      if (useReader.getState().selectedDocId !== docId) return
      scrollToPage(target)
      restoredForDoc.current = docId
      useReader.getState().markProgressRestored(docId)
    }, 60)
    return () => clearTimeout(t)
  }, [pdfDoc, pageBase, docId, scrollToPage, progressRestoredFor])

  // Auto-dismiss the "jumped but no highlight" toast
  useEffect(() => {
    if (!citationToast) return
    const t = setTimeout(() => clearCitationToast(), 2800)
    return () => clearTimeout(t)
  }, [citationToast, clearCitationToast])

  // Which page the reader is on, decided by area on screen rather than by
  // whichever IntersectionObserver happened to fire last.
  //
  // That observer runs with rootMargin: "300px" because it also drives
  // render/teardown, so three or four pages qualify as "visible" at once and
  // the reported page was simply the last callback to run - usually the
  // bottom-most. Reading was unaffected, but with reading progress being
  // saved it meant reopening a document crept forward a page each time.
  const updateCurrentPage = useCallback(() => {
    if (useReader.getState().progressRestoredFor !== docId) return
    if (suppressObserverRef.current) return
    const container = scrollContainerRef.current
    if (!container) return
    const top = container.getBoundingClientRect().top
    const bottom = top + container.clientHeight

    let best: { page: number; area: number } | null = null
    for (const el of Array.from(
      container.querySelectorAll<HTMLElement>('[id^="pdf-page-container-"]')
    )) {
      const r = el.getBoundingClientRect()
      const visible = Math.min(r.bottom, bottom) - Math.max(r.top, top)
      if (visible <= 0) continue
      const page = Number(el.id.replace("pdf-page-container-", ""))
      // Strict >: ties go to the lower page number, so a two-page-straddling
      // scroll position settles on the earlier one instead of oscillating.
      if (!best || visible > best.area) best = { page, area: visible }
    }
    if (best && best.page !== lastScrolledPage.current) {
      lastScrolledPage.current = best.page
      setPage(best.page)
    }
  }, [docId, setPage])

  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    let frame = 0
    const onScroll = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        updateCurrentPage()
      })
    }
    container.addEventListener("scroll", onScroll, { passive: true })
    // Once at mount too: opening a document that restores to a saved page
    // produces no scroll event if it was already there.
    onScroll()
    return () => {
      container.removeEventListener("scroll", onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [updateCurrentPage, pdfDoc])

  const goToPage = (pageNum: number) => {
    if (pageNum < 1 || (numPages && pageNum > numPages)) return
    // An explicit jump (spinner, outline, prev/next, in-doc link) wins over
    // follow-reading: otherwise the per-sentence re-centering fires a
    // competing smooth scroll ~100ms in, cancels the jump's animation, and
    // the view snaps back to the reading page. Same rule as manual
    // wheel/touch (see pause()). Follow re-engages on the next readPage or
    // via "Return to reading".
    setFollowReading(false)
    setPage(pageNum)
    scrollToPage(pageNum)
  }

  const OutlineNode = ({ item, depth }: { item: OutlineItem; depth: number }) => {
    const [collapsed, setCollapsed] = useState(false)
    const hasChildren = item.items.length > 0
    return (
      <div className="text-xs">
        <div
          className={`flex cursor-pointer items-center gap-1 rounded-md px-2 py-1.5 transition-colors hover:bg-surface ${
            currentPage === item.pageNumber ? "font-semibold text-indigo" : "text-cream-dim"
          }`}
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
          onClick={() => item.pageNumber && goToPage(item.pageNumber)}
        >
          {hasChildren ? (
            <button
              onClick={(e) => {
                e.stopPropagation()
                setCollapsed(!collapsed)
              }}
              className="p-0.5 text-cream-dim hover:text-cream"
            >
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${collapsed ? "-rotate-90" : ""}`}
              />
            </button>
          ) : (
            <div className="w-4" />
          )}
          <span className="flex-1 truncate">{item.title}</span>
          {item.pageNumber && (
            <span className="ml-auto rounded bg-surface px-1 text-[10px] text-cream-dim">
              {item.pageNumber}
            </span>
          )}
        </div>
        {hasChildren && !collapsed && (
          <div className="mt-0.5 space-y-0.5">
            {item.items.map((child, i) => (
              <OutlineNode key={i} item={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-void">
      {/* Toolbar */}
      <div className="reader-scroll flex shrink-0 items-center justify-between gap-2 overflow-x-auto border-b border-surface bg-panel px-2 py-1.5 sm:px-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowOutline(!showOutline)}
            className={`rounded p-1.5 transition-colors ${
              showOutline ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:bg-surface hover:text-cream"
            }`}
            title="Toggle outline"
            disabled={outline.length === 0}
          >
            <List className="h-4 w-4" />
          </button>
          <div className="h-4 w-px bg-surface" />
          <button
            onClick={() => applyZoom(Math.max(scale - 0.25, 0.5))}
            title="Zoom out"
            disabled={scale <= 0.5}
            className="rounded p-1.5 text-cream-dim hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="w-10 text-center text-xs tabular-nums text-cream-dim">
            {Math.round(scale * 100)}%
          </span>
          <button
            onClick={() => applyZoom(Math.min(scale + 0.25, 3))}
            title="Zoom in"
            disabled={scale >= 3}
            className="rounded p-1.5 text-cream-dim hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            onClick={() => applyZoom(null)}
            className={`rounded p-1.5 transition-colors ${
              zoomOverride === null
                ? "bg-indigo/15 text-indigo"
                : "text-cream-dim hover:bg-surface hover:text-cream"
            }`}
            title={zoomOverride === null ? "Fitting page to width" : "Fit page to width"}
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <div className="h-4 w-px bg-surface" />
          <button
            onClick={toggleInvertPdf}
            className={`rounded p-1.5 transition-colors ${
              invertPdf ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:bg-surface hover:text-cream"
            }`}
            title={invertPdf ? "Original page colors" : "Dark page colors"}
          >
            {invertPdf ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <button
            onClick={() => setSelectMode((v) => !v)}
            className={`rounded p-1.5 transition-colors ${
              selectMode ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:bg-surface hover:text-cream"
            }`}
            title={
              selectMode
                ? "Select mode on: drag or double-click to highlight. Click the speaker to read."
                : "Click a line to hear it. Double-click or long-press a word to select it."
            }
          >
            {selectMode ? <TextSelect className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
          </button>
          <button
            data-testid="docling-overlay-toggle"
            aria-label="Toggle reading overlay"
            aria-pressed={showOverlay}
            disabled={layout.checking || layoutResetting}
            onClick={() => {
              setShowOverlay((prev) => layout.status === "not_requested" ? true : !prev)
              if (layout.status === "not_requested") void layout.request()
            }}
            className={`rounded p-1.5 transition-colors ${
              showOverlay ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:bg-surface hover:text-cream"
            }`}
            title={showOverlay ? "Hide reading overlay" : "Show reading overlay"}
          >
            <Layers className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className="rounded p-1.5 text-cream-dim hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              value={currentPage}
              onChange={(e) => goToPage(parseInt(e.target.value) || 1)}
              className="w-12 rounded border border-surface bg-void px-2 py-1 text-center text-xs font-semibold text-cream focus:outline-none focus:ring-1 focus:ring-indigo"
              min={1}
              max={numPages || 1}
            />
            <span className="text-xs text-cream-dim">/ {numPages}</span>
          </div>
          <button
            onClick={() => goToPage(currentPage + 1)}
            disabled={numPages > 0 && currentPage >= numPages}
            className="rounded p-1.5 text-cream-dim hover:bg-surface hover:text-cream disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {(layout.requesting || layout.status === "processing") && (
        <div role="status" data-testid="layout-analysis-status" className="shrink-0 border-b border-surface bg-panel px-3 py-2 text-xs text-cream-dim">
          Analyzing layout… You can keep reading.
        </div>
      )}
      {(layout.error || layout.status === "failed") && (
        <div role="status" className="flex shrink-0 flex-wrap items-center gap-2 border-b border-surface bg-panel px-3 py-2 text-xs text-rose-300">
          <span>{layout.error || "Layout analysis failed."}</span>
          <button type="button" onClick={() => { setShowOverlay(true); void layout.retry() }} disabled={layout.requesting || layoutResetting} className="underline disabled:opacity-50">
            Retry layout analysis
          </button>
        </div>
      )}

      <div className="relative flex min-w-0 flex-1 overflow-hidden">
        {showOutline && outline.length > 0 && (
          <>
            {/* Backdrop: click outside the drawer to close */}
            <div
              className="absolute inset-0 z-20 bg-black/20"
              onClick={() => setShowOutline(false)}
            />
            <div className="glass absolute inset-y-0 left-0 z-30 flex w-72 shrink-0 flex-col border-r border-surface shadow-glow">
              <div className="flex items-center justify-between px-3 pt-3">
                <h4 className="text-[10px] font-semibold uppercase tracking-wider text-cream-dim">
                  Outline
                </h4>
                <button
                  onClick={() => setShowOutline(false)}
                  title="Close outline"
                  className="rounded p-1 text-cream-dim hover:bg-surface hover:text-cream"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="reader-scroll select-none space-y-1 overflow-y-auto p-3">
                {outline.map((item, i) => (
                  <OutlineNode key={i} item={item} depth={0} />
                ))}
              </div>
            </div>
          </>
        )}

        <div
          ref={scrollContainerRef}
          className="reader-scroll relative flex min-w-0 flex-1 flex-col items-center gap-6 overflow-auto p-2 sm:p-6"
        >
          {!followReading && (ttsStatus === "playing" || ttsStatus === "paused") && (
            <button
              onClick={returnToReading}
              className="glass fixed bottom-14 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-indigo/40 px-3 py-1.5 text-xs font-medium text-indigo shadow-glow transition-colors hover:text-cream"
              title="Scroll back to the line being read"
            >
              <LocateFixed className="h-3.5 w-3.5" />
              Back to reading
            </button>
          )}
          {citationToast && (
            <div
              className="glass fixed bottom-14 left-1/2 z-30 -translate-x-1/2 rounded-full border border-surface px-3 py-1.5 text-xs text-cream-dim shadow-glow"
              role="status"
            >
              {citationToast}
            </div>
          )}
          {loading ? (
            <div className="flex h-full w-full items-center justify-center">
              <LoadingSpinner />
            </div>
          ) : !pdfDoc ? (
            <div className="flex h-full items-center justify-center text-sm text-cream-dim">
              Failed to load document
            </div>
          ) : (
            pageList.map((pageNum) => {
              const mounted =
                pageNum >= mountRange.lo && pageNum <= mountRange.hi
              if (!mounted) {
                // Height-only shell: keeps scroll offsets correct and gives
                // updateCurrentPage / scrollToPage a stable id to measure.
                return (
                  <div
                    key={pageNum}
                    id={`pdf-page-container-${pageNum}`}
                    className={`relative shrink-0 overflow-hidden rounded border border-surface shadow-lg ${
                      invertPdf ? "bg-[#101014]" : "bg-white"
                    }`}
                    style={{
                      width: `${fallbackSize?.width ?? 600}px`,
                      height: `${fallbackSize?.height ?? 850}px`,
                    }}
                    aria-hidden
                  />
                )
              }
              return (
                <PdfPage
                  key={pageNum}
                  pdfDoc={pdfDoc}
                  docId={docId}
                  pageNum={pageNum}
                  scale={scale}
                  fallbackSize={fallbackSize}
                  onNavigate={goToPage}
                  selectMode={selectMode}
                  touchSelect={touchSelect}
                  showOverlay={showOverlay && layout.status === "ready" && !layoutResetting}
                  pageTextVersion={`${pageTextVersion}:${layoutVersion}`}
                />
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

interface PdfPageProps {
  pdfDoc: any
  docId: number
  pageNum: number
  scale: number
  /** Size to reserve before this page has rendered, so scroll offsets are
      right for pages that have never been on screen. */
  fallbackSize: { width: number; height: number } | null
  onNavigate: (pageNum: number) => void
  selectMode: boolean
  touchSelect: boolean
  showOverlay: boolean
  pageTextVersion: string
}

type PdfLink = PdfLinkRect

/**
 * One lazily-rendered PDF page: canvas + PDF.js text layer.
 * Clicking a text span starts TTS from that sentence; during playback the
 * active sentence's spans get an amber highlight and stay scrolled into view.
 */
function PdfPage({ pdfDoc, docId, pageNum, scale, onNavigate, fallbackSize, selectMode, touchSelect, showOverlay, pageTextVersion }: PdfPageProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const textLayerRef = useRef<HTMLDivElement>(null)
  const [rendered, setRendered] = useState(false)
  // True only after canvas + text layer are indexed. Citation matching must
  // wait for this - `rendered` alone used to flip true mid-render, which ate
  // pendingHighlight before spanDataRef was ready (the "click twice" bug).
  const [textReady, setTextReady] = useState(false)
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null)
  const [pageData, setPageData] = useState<PageText | null>(null)
  const [overlayError, setOverlayError] = useState<string | null>(null)

  useEffect(() => {
    if (!showOverlay) return
    let active = true
    loadPageText(docId, pageNum)
      .then((data) => {
        if (active) {
          setPageData(data)
          setOverlayError(null)
        }
      })
      .catch((err) => {
        if (active) setOverlayError("Failed to load reading layout.")
        console.error("Failed to load page text for overlay:", err)
      })
    return () => {
      active = false
    }
  }, [docId, pageNum, showOverlay, pageTextVersion])
  const [links, setLinks] = useState<PdfLink[]>([])
  const [hoverLinkKey, setHoverLinkKey] = useState<string | null>(null)
  const hoverLink = hoverLinkKey ? links.find((l) => l.key === hoverLinkKey) ?? null : null
  const renderInFlightRef = useRef(false)
  const isIntersectingRef = useRef(false)
  const lastRenderedScaleRef = useRef<number | null>(null)
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null)
  const textLayerTaskRef = useRef<{ cancel: () => void } | null>(null)
  const renderIdRef = useRef(0)
  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const ensureRenderedRef = useRef<() => Promise<void>>(async () => {})
  const tearDownRef = useRef<() => void>(() => {})
  const isPendingTargetRef = useRef(false)

  // Per-span [start, end) offsets within the concatenated text-layer string
  const spanDataRef = useRef<{
    spans: HTMLElement[]
    ranges: [number, number][]
    rawText: string
    normLayer: NormalizedText
  } | null>(null)
  // Pending "end of flash" timeout - see the citation-flash effect below.
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const tts = useReader((s) => s.tts)
  const followReading = useReader((s) => s.followReading)
  const invertPdf = useReader((s) => s.invertPdf)
  const pendingHighlight = useReader((s) => s.pendingHighlight)
  const clearPendingHighlight = useReader((s) => s.clearPendingHighlight)
  const failPendingHighlight = useReader((s) => s.failPendingHighlight)
  const isPendingTarget = pendingHighlight?.docId === docId && pendingHighlight?.page === pageNum

  useEffect(() => {
    let active = true
    pdfDoc
      .getPage(pageNum)
      .then((page: any) => {
        const viewport = page.getViewport({ scale })
        if (active) setDimensions({ width: viewport.width, height: viewport.height })
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [pdfDoc, pageNum, scale])

  const tearDown = useCallback(() => {
    if (renderTaskRef.current) {
      try {
        renderTaskRef.current.cancel()
      } catch {
        /* already finished */
      }
      renderTaskRef.current = null
    }
    if (textLayerTaskRef.current) {
      try {
        textLayerTaskRef.current.cancel()
      } catch {
        /* already finished */
      }
      textLayerTaskRef.current = null
    }
    setRendered(false)
    setTextReady(false)
    lastRenderedScaleRef.current = null
    spanDataRef.current = null
    setLinks([])
    setHoverLinkKey(null)
    renderInFlightRef.current = false
    const canvas = canvasRef.current
    canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height)
    if (textLayerRef.current) textLayerRef.current.innerHTML = ""
  }, [])

  const ensureRendered = useCallback(async () => {
    if (!canvasRef.current) return
    if (textReady && lastRenderedScaleRef.current === scale) return
    if (renderInFlightRef.current && lastRenderedScaleRef.current === scale) return

    if (renderTaskRef.current) {
      try {
        renderTaskRef.current.cancel()
      } catch {
        /* already finished */
      }
      renderTaskRef.current = null
    }
    if (textLayerTaskRef.current) {
      try {
        textLayerTaskRef.current.cancel()
      } catch {
        /* already finished */
      }
      textLayerTaskRef.current = null
    }

    const renderId = ++renderIdRef.current
    const genScale = scale
    renderInFlightRef.current = true
    // Keep the previous bitmap on screen while the new scale renders so zoom
    // does not flash a blank "Page N" placeholder.
    setRendered(true)
    try {
      const pdfjs = await import("pdfjs-dist")
      const page = await pdfDoc.getPage(pageNum)
      if (genScale !== scaleRef.current || renderId !== renderIdRef.current) return
      // Two viewports, and conflating them was a real bug: `viewport` is the
      // page's CSS box, `renderViewport` is the canvas backing store, drawn at
      // SUPERSAMPLE times the size so text stays crisp. The old code sized the
      // CSS box from the supersampled viewport, so every page was laid out 50%
      // wider than its own zoom level - a 612pt page occupied 918px at "100%"
      // and overflowed any column narrower than that. Fit-to-width could not
      // help, because the number it fitted was not the number used for layout.
      const viewport = page.getViewport({ scale: genScale })
      const renderViewport = page.getViewport({ scale: genScale * SUPERSAMPLE })
      const canvas = canvasRef.current
      if (!canvas) return

      // Draw offscreen, then swap. Assigning canvas.width clears the bitmap,
      // which is the zoom flash.
      const offscreen = document.createElement("canvas")
      offscreen.width = renderViewport.width
      offscreen.height = renderViewport.height
      const offCtx = offscreen.getContext("2d")
      if (!offCtx) return

      const task = page.render({ canvasContext: offCtx, viewport: renderViewport })
      renderTaskRef.current = task
      await task.promise
      renderTaskRef.current = null
      if (genScale !== scaleRef.current || renderId !== renderIdRef.current) return

      canvas.width = offscreen.width
      canvas.height = offscreen.height
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      const liveCtx = canvas.getContext("2d")
      liveCtx?.drawImage(offscreen, 0, 0)

      const layerDiv = textLayerRef.current
      if (layerDiv) {
        const staging = document.createElement("div")
        staging.style.setProperty("--scale-factor", String(viewport.scale))
        const textLayer = new pdfjs.TextLayer({
          textContentSource: page.streamTextContent(),
          container: staging,
          viewport,
        })
        textLayerTaskRef.current = textLayer
        try {
          await textLayer.render()
        } finally {
          if (textLayerTaskRef.current === textLayer) {
            textLayerTaskRef.current = null
          }
        }
        if (genScale !== scaleRef.current || renderId !== renderIdRef.current) return
        disableNonReadingHits(staging)
        ensureEndOfContent(staging)
        layerDiv.style.setProperty("--scale-factor", String(viewport.scale))
        layerDiv.replaceChildren(...Array.from(staging.childNodes))

        const spans = leafTextSpans(layerDiv)
        const ranges: [number, number][] = []
        let offset = 0
        let layerText = ""
        for (const span of spans) {
          const t = span.textContent || ""
          ranges.push([offset, offset + t.length])
          layerText += t + " "
          offset += t.length + 1
        }
        spanDataRef.current = {
          spans,
          ranges,
          rawText: layerText,
          normLayer: normalizeWithMap(layerText),
        }
      }

      // Hyperlinks baked into the PDF. PDF.js gives rects in PDF user space
      // with a bottom-left origin; convertToViewportRectangle maps them into
      // the same top-left pixel space the canvas and text layer already use,
      // and this container is sized to exactly that viewport, so the numbers
      // drop straight into CSS with no further scaling.
      try {
        const annotations = await page.getAnnotations({ intent: "display" })
        if (genScale !== scaleRef.current || renderId !== renderIdRef.current) return
        setLinks(
          annotations
            .filter((a: any) => a.subtype === "Link" && (a.url || a.dest))
            .map((a: any, i: number) => {
              const [x1, y1, x2, y2] = viewport.convertToViewportRectangle(a.rect)
              return {
                key: `${pageNum}-${i}`,
                left: Math.min(x1, x2),
                top: Math.min(y1, y2),
                width: Math.abs(x2 - x1),
                height: Math.abs(y2 - y1),
                url: a.url,
                dest: a.dest,
              }
            }),
        )
      } catch {
        // A page without an annotation dictionary just has no links.
      }

      if (renderId !== renderIdRef.current) return
      lastRenderedScaleRef.current = genScale
      setTextReady(true)
    } catch (err: any) {
      if (renderId !== renderIdRef.current) return
      const name = err?.name
      const msg = err?.message || String(err)
      if (
        name === "RenderingCancelledException" ||
        name === "AbortException" ||
        msg.includes("Worker task was terminated") ||
        msg.includes("TextLayer task cancelled")
      ) {
        // Normal cancellation when zoom/scroll/unmount interrupted the render.
        // Canvas already drawn stays visible so the user does not see a blank page.
        return
      }
      console.error("Render failed for page:", pageNum, err)
      tearDown()
    } finally {
      if (renderId === renderIdRef.current) renderInFlightRef.current = false
    }
  }, [pdfDoc, pageNum, scale, textReady, tearDown])

  ensureRenderedRef.current = ensureRendered
  tearDownRef.current = tearDown
  isPendingTargetRef.current = isPendingTarget

  // Lazy render on visibility. Observer identity is stable across zoom so a
  // scale change cannot disconnect/reconnect and tear the page down mid-render.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          isIntersectingRef.current = entry.isIntersecting
          if (entry.isIntersecting) {
            void ensureRenderedRef.current()
          } else if (!isPendingTargetRef.current) {
            tearDownRef.current()
          }
        })
      },
      { threshold: 0.1, rootMargin: "300px" }
    )
    observer.observe(container)
    return () => observer.disconnect()
  }, [pageNum])

  // Re-render when scale changes (or a citation jump targets this page) without
  // relying on the observer firing again.
  useEffect(() => {
    if (isPendingTarget || isIntersectingRef.current) void ensureRendered()
  }, [isPendingTarget, ensureRendered])

  // After the citation flash is done (pending cleared), drop off-screen pages
  // that were only kept alive for the jump.
  useEffect(() => {
    if (!isPendingTarget && !isIntersectingRef.current && (rendered || textReady)) {
      tearDown()
    }
  }, [isPendingTarget, rendered, textReady, tearDown])

  // Locate a span's sentence in the extracted text -> read aloud from there
  const readFromSpan = async (target: HTMLElement) => {
    if (!spanDataRef.current) return
    const { spans, ranges, normLayer } = spanDataRef.current
    const idx = spans.indexOf(target)
    if (idx === -1) return

    // Build a distinctive needle: clicked span text extended with following spans
    let needle = spans[idx].textContent || ""
    let j = idx + 1
    while (needle.replace(/\s+/g, " ").trim().length < 24 && j < spans.length) {
      needle += " " + (spans[j].textContent || "")
      j++
    }

    try {
      const pageText = await loadPageText(docId, pageNum)
      if (pageText.sentences.length === 0) return

      const container = containerRef.current
      if (container && pageText.doclingRegions.length > 0) {
        const containerRect = container.getBoundingClientRect()
        const targetRect = target.getBoundingClientRect()
        if (containerRect.width > 0 && containerRect.height > 0) {
          const centerX = (targetRect.left + targetRect.right) / 2
          const centerY = (targetRect.top + targetRect.bottom) / 2
          const normX = (centerX - containerRect.left) / containerRect.width
          const normY = (centerY - containerRect.top) / containerRect.height
          if (isPointInRejectedDoclingRegion(normX, normY, pageText.doclingRegions)) {
            return
          }
        }
      }

      // Proportional hint dampens repeated-phrase mismatches
      const hint = Math.floor(
        (ranges[idx][0] / Math.max(normLayer.map.length, 1)) * pageText.normContent.norm.length
      )
      const range = findRange(pageText.normContent, needle, hint)
      if (!range) return

      const sentenceIdx = pageText.sentences.findIndex(
        (sentenceItem) => range.start < sentenceItem.end && range.end > sentenceItem.start
      )
      if (sentenceIdx === -1) return

      void ttsEngine.readPage(
        docId,
        pageNum,
        sentenceIdx,
        dehyphenateForSpeech(pageText.sentences),
      )
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        useReader.getState().failPendingHighlight("Text extraction is in progress. Speech will be ready once extraction completes.")
      } else {
        console.error("Click-to-read failed:", err)
      }
    }
  }

  const allNotes = useNotes((s) => s.notes)
  const notesDocId = useNotes((s) => s.docId)
  const noteColors = useNotes((s) => s.colors)
  const setNoteSelection = useNotes((s) => s.setSelection)
  const copyOnSelect = useReader((s) => s.copyOnSelect)
  const pointerRef = useRef<{
    x: number
    y: number
    moved: boolean
    longPress: boolean
    onText: boolean
    anchorX: number | null
    anchorY: number | null
    pointerId: number
  } | null>(null)
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const ttsClickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastGestureMoved = useRef(false)

  const clearLongPress = () => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current)
      longPressTimer.current = null
    }
  }

  const openBarFromSelection = (fallbackX?: number, fallbackY?: number) => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) return
    const text = sel.toString().replace(/\s+/g, " ").trim()
    if (text.length < 1) return
    const layer = textLayerRef.current
    const pages = pagesFromSelection(sel)
    if (!layer) return
    // Accept a selection that starts or ends on this page so a drag across
    // a page break still opens the bar once, on the page that received up.
    if (pages) {
      if (pageNum < pages.start || pageNum > pages.end) return
    } else if (!sel.anchorNode || !layer.contains(sel.anchorNode)) {
      return
    }
    if (copyOnSelect) copyText(text)
    let x = fallbackX
    let y = fallbackY
    if (sel.rangeCount > 0) {
      const rect = sel.getRangeAt(0).getBoundingClientRect()
      x = rect.left + rect.width / 2
      y = rect.bottom
    }
    setNoteSelection({
      docId,
      page: pages?.start ?? pageNum,
      endPage: pages && pages.end !== pages.start ? pages.end : undefined,
      quote: text,
      x: x ?? 0,
      y: y ?? 0,
    })
  }

  const handlePointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const layer = textLayerRef.current
    const onText = isReadingSpan(e.target, layer)
    lastGestureMoved.current = false
    pointerRef.current = {
      x: e.clientX,
      y: e.clientY,
      moved: false,
      longPress: false,
      onText,
      anchorX: onText ? e.clientX : null,
      anchorY: onText ? e.clientY : null,
      pointerId: e.pointerId,
    }
    // Starting in the margin/whitespace must not let the browser anchor
    // the selection at the first glyph on the page (title, running head).
    // Capture the pointer so a drag onto the next page still updates here.
    if (!onText) {
      e.preventDefault()
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      } catch {
        /* pointer capture is best-effort */
      }
    }
    layer?.classList.add("selecting")
    clearLongPress()
    if (ttsClickTimer.current) {
      clearTimeout(ttsClickTimer.current)
      ttsClickTimer.current = null
    }
    const startX = e.clientX
    const startY = e.clientY
    longPressTimer.current = setTimeout(() => {
      const p = pointerRef.current
      if (!p || p.moved) return
      const word = selectWordAtPoint(startX, startY)
      if (!word) return
      p.longPress = true
      if (touchSelect) {
        try {
          layer?.setPointerCapture(p.pointerId)
        } catch {
          /* capture is best-effort */
        }
      }
      openBarFromSelection(startX, startY)
    }, 380)
  }

  const handlePointerMove = (e: React.PointerEvent) => {
    const p = pointerRef.current
    const layer = textLayerRef.current
    if (!p) {
      const box = containerRef.current?.getBoundingClientRect()
      if (!box) return
      const hit = hitTestLink(links, e.clientX - box.left, e.clientY - box.top)
      const key = hit?.key ?? null
      setHoverLinkKey((cur) => (cur === key ? cur : key))
      return
    }
    if (!layer) return
    if (hoverLinkKey) setHoverLinkKey(null)
    if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 6) {
      p.moved = true
      lastGestureMoved.current = true
      clearLongPress()
    }
    // Phone: after the long-press has locked a word, sliding grows the
    // range. Desktop never takes this branch.
    if (p.longPress && touchSelect) {
      e.preventDefault()
      const text = extendSelectionEdge("end", e.clientX, e.clientY)
      if (text) openBarFromSelection(e.clientX, e.clientY)
      return
    }
    if (p.onText) return
    e.preventDefault()
    const over = document.elementFromPoint(e.clientX, e.clientY)
    const overLayer = over instanceof Element ? textLayerOf(over) : null
    if (p.anchorX == null) {
      if (!overLayer || !isReadingSpan(over, overLayer)) return
      p.anchorX = e.clientX
      p.anchorY = e.clientY
      selectWordAtPoint(e.clientX, e.clientY)
      return
    }
    const ax = p.anchorX
    const ay = p.anchorY
    if (ax == null || ay == null) return
    // No layer lock: a captured margin-drag can cross onto the next page.
    setSelectionFromPoints(ax, ay, e.clientX, e.clientY)
  }

  const handlePointerUp = (e: React.PointerEvent) => {
    clearLongPress()
    textLayerRef.current?.classList.remove("selecting")
    const p = pointerRef.current
    pointerRef.current = null
    if (p?.longPress) return
    if (p && !p.onText && p.anchorX == null) {
      window.getSelection()?.removeAllRanges()
      return
    }
    // A click that just dismissed the floating bar must not reopen it
    // on the word under the pointer.
    if (p && !p.moved && Date.now() - useNotes.getState().selectionClearedAt < 400) {
      window.getSelection()?.removeAllRanges()
      return
    }
    // Tiny drag that accidentally selected a long span: snap to the word
    // under the pointer instead of keeping the whole paragraph.
    const quote = selectionQuote()
    if (p && !p.moved && quote.split(/\s+/).length > 6) {
      const word = selectWordAtPoint(e.clientX, e.clientY)
      if (word) {
        openBarFromSelection(e.clientX, e.clientY)
        return
      }
    }
    openBarFromSelection(e.clientX, e.clientY)
  }

  useEffect(() => {
    if (!textReady || !spanDataRef.current) return
    const { spans, ranges, normLayer } = spanDataRef.current
    const pageNotes =
      notesDocId === docId
        ? allNotes.filter((n) => {
            const start = n.page_number
            const end = n.end_page ?? n.page_number
            return pageNum >= start && pageNum <= end
          })
        : []
    applyNoteHighlights(spans, ranges, normLayer, pageNotes, noteColors)
  }, [textReady, allNotes, notesDocId, pageNum, docId, noteColors])

  useEffect(() => {
    return () => {
      clearLongPress()
      if (ttsClickTimer.current) clearTimeout(ttsClickTimer.current)
    }
  }, [])

  const hitLink = (clientX: number, clientY: number): PdfLink | null => {
    const box = containerRef.current?.getBoundingClientRect()
    if (!box) return null
    return hitTestLink(links, clientX - box.left, clientY - box.top)
  }

  const openLink = (link: PdfLink) => {
    if (link.url) {
      if (!/^https?:\/\//i.test(link.url) && !link.url.startsWith("mailto:")) {
        console.warn("Blocked non-http(s) link navigation:", link.url)
        return
      }
      try {
        window.open(link.url, "_blank", "noopener,noreferrer")
      } catch (err) {
        console.warn("Failed to open link:", err)
      }
      return
    }
    if (link.dest) void followDest(link.dest)
  }

  const readFromPoint = (x: number, y: number) => {
    const layer = textLayerRef.current
    const span = document
      .elementsFromPoint(x, y)
      .find((el) => el.tagName === "SPAN" && layer?.contains(el) && isReadingSpan(el, layer))
    if (span) void readFromSpan(span as HTMLElement)
  }

  const handleTextClick = (e: React.MouseEvent) => {
    const link = hitLink(e.clientX, e.clientY)
    const intent = pdfClickIntent({
      dragged: lastGestureMoved.current,
      hasSelection: !!selectionQuote(),
      altKey: e.altKey,
      overLink: !!link,
      selectMode,
      detail: e.detail,
    })
    if (intent === "ignore") return
    if (intent === "select") {
      if (e.detail > 1) {
        if (ttsClickTimer.current) {
          clearTimeout(ttsClickTimer.current)
          ttsClickTimer.current = null
        }
        openBarFromSelection(e.clientX, e.clientY)
      }
      return
    }
    if (
      intent === "read" &&
      Date.now() - useNotes.getState().selectionClearedAt < 400
    ) {
      return
    }
    if (intent === "follow-link" && link) {
      if (ttsClickTimer.current) {
        clearTimeout(ttsClickTimer.current)
        ttsClickTimer.current = null
      }
      e.preventDefault()
      openLink(link)
      return
    }
    const target = e.target as HTMLElement
    if (ttsClickTimer.current) {
      clearTimeout(ttsClickTimer.current)
      ttsClickTimer.current = null
    }
    if (selectionQuote()) return
    if (target.tagName === "SPAN") void readFromSpan(target)
    else readFromPoint(e.clientX, e.clientY)
  }

  const handleAuxClick = (e: React.MouseEvent) => {
    if (e.button !== 1) return
    const link = hitLink(e.clientX, e.clientY)
    if (!link?.url) return
    e.preventDefault()
    window.open(link.url, "_blank", "noopener,noreferrer")
  }

  /** Follow an internal link (a table-of-contents entry, a cross-reference)
   * by resolving its destination to a page number. */
  const followDest = async (dest: unknown) => {
    try {
      const resolved = typeof dest === "string" ? await pdfDoc.getDestination(dest) : dest
      const ref = (resolved as any[])?.[0]
      const index =
        ref && typeof ref === "object" ? await pdfDoc.getPageIndex(ref) : (ref as number)
      if (typeof index === "number") onNavigate(index + 1)
    } catch (err) {
      console.error("Could not resolve link destination:", err)
    }
  }

  // Highlight the active sentence's spans while this page is being read
  useEffect(() => {
    const data = spanDataRef.current
    if (!data || !textReady) return
    const clear = () =>
      data.spans.forEach((s) =>
        s.classList.remove("tts-sentence-active", "tts-sentence-preparing")
      )

    const isThisPage = tts.docId === docId && tts.page === pageNum
    if (!isThisPage || tts.status === "idle") {
      clear()
      return
    }

    let cancelled = false
    loadPageText(docId, pageNum).then((pageText) => {
      if (cancelled || !spanDataRef.current) return
      // A layout refresh changes future narration. Keep the current playback
      // mapped to its own sentence snapshot until another read starts.
      const playingSentences = ttsEngine.getSentences(docId, pageNum)
      const sentence = (playingSentences.length ? playingSentences : pageText.sentences)[tts.sentenceIndex]
      if (!sentence) return
      const range = findRange(data.normLayer, pageText.content.slice(sentence.start, sentence.end))
      clear()
      if (!range) return
      const cls = tts.status === "preparing" ? "tts-sentence-preparing" : "tts-sentence-active"
      let first: HTMLElement | null = null
      data.ranges.forEach(([s, e], i) => {
        if (s < range.end && e > range.start) {
          data.spans[i].classList.add(cls)
          if (!first) first = data.spans[i]
        }
      })
      if (first && tts.status === "playing" && useReader.getState().followReading) {
        ;(first as HTMLElement).scrollIntoView({ behavior: "smooth", block: "center" })
      }
    }).catch(() => {
      // The PDF remains usable while extracted text is still being prepared.
      // loadPageText evicts this failure so the next revision/click retries it.
    })
    return () => {
      cancelled = true
    }
    // followReading in deps so re-enabling follow immediately re-centers
  }, [tts.docId, tts.page, tts.sentenceIndex, tts.status, pageNum, docId, textReady, followReading, pageTextVersion])

  // Flash-highlight a citation excerpt once this page's text layer is ready.
  //
  // pendingHighlight is deliberately kept set for the ENTIRE flash, not
  // cleared the moment the highlight is applied. Clearing it early flips
  // isPendingTarget to false while the smooth-scroll from the click is
  // still animating; IntersectionObserver only reports "intersecting" once
  // the browser has actually caught up with that scroll, which lags behind
  // the JS-triggered animation. In that gap, the "drop off-screen pages"
  // effect above (`!isPendingTarget && !isIntersectingRef.current`) saw
  // both conditions true and tore the text layer straight back down -
  // deleting the highlight before it was ever painted. That produced
  // exactly the reported symptom: clicking a citation the first time
  // scrolled to the page but showed nothing (torn down before you could
  // see it), the second click worked (the page was already in view from
  // the first click's scroll by then, so isIntersectingRef was already
  // true), and clicking a different citation and back reset it into that
  // same "first click" state again. Keeping pendingHighlight (and
  // therefore isPendingTarget) set until the flash's own timeout elapses
  // means this page can never be torn down mid-animation.
  useEffect(() => {
    if (!pendingHighlight || pendingHighlight.docId !== docId || pendingHighlight.page !== pageNum) return
    if (!textReady) return
    const data = spanDataRef.current
    if (!data) return

    // A re-click on the same citation while an earlier flash on this page
    // is still running must restart cleanly, not race the old timeout.
    if (flashTimeoutRef.current) {
      clearTimeout(flashTimeoutRef.current)
      flashTimeoutRef.current = null
    }

    const exactEvidence = pendingHighlight.evidence?.exact?.trim()
    if (!exactEvidence) {
      // Legacy citation or absent evidence: land on page without highlighting wrong passage.
      clearPendingHighlight()
      return
    }

    const match = findCitationEvidenceRange(data.rawText, exactEvidence)
    if (match.status === "ambiguous") {
      failPendingHighlight(`Jumped to page ${pageNum} - passage ambiguous`)
      return
    }
    if (match.status === "not_found") {
      failPendingHighlight(`Jumped to page ${pageNum} - passage not found`)
      return
    }

    // Retire any earlier active citation flash on this page before painting the new citation
    data.spans.forEach((s) => s.classList.remove("citation-flash"))

    const marked: HTMLElement[] = []
    data.ranges.forEach(([s, e], i) => {
      if (s < match.end && e > match.start) {
        const span = data.spans[i]
        span.classList.remove("citation-flash")
        void span.offsetWidth
        span.classList.add("citation-flash")
        marked.push(span)
      }
    })
    if (marked.length === 0) {
      failPendingHighlight(
        `Jumped to page ${pageNum} - passage not found on the page layer`
      )
      return
    }

    marked[0].scrollIntoView({ behavior: "smooth", block: "center" })
    flashTimeoutRef.current = setTimeout(() => {
      marked.forEach((s) => s.classList.remove("citation-flash"))
      clearPendingHighlight()
      flashTimeoutRef.current = null
    }, 2600)
  }, [
    pendingHighlight,
    docId,
    pageNum,
    textReady,
    clearPendingHighlight,
    failPendingHighlight,
  ])

  // Unmount safety net only - the effect above already cancels/replaces its
  // own timeout on every re-run, this just covers the page being torn down
  // or navigated away from entirely while a flash timeout is outstanding.
  useEffect(() => {
    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current)
    }
  }, [])

  return (
    <div
      ref={containerRef}
      id={`pdf-page-container-${pageNum}`}
      className={`relative shrink-0 overflow-hidden rounded border border-surface shadow-lg ${
        invertPdf ? "bg-[#101014]" : "bg-white"
      }`}
      style={{
        width: `${dimensions?.width ?? fallbackSize?.width ?? 600}px`,
        height: `${dimensions?.height ?? fallbackSize?.height ?? 850}px`,
      }}
    >
      {/* Inverting only the canvas keeps text-layer highlights in true colors */}
      <canvas
        ref={canvasRef}
        style={invertPdf ? { filter: "invert(0.94) hue-rotate(180deg)" } : undefined}
      />
      <div
        ref={textLayerRef}
        onClick={handleTextClick}
        onAuxClick={handleAuxClick}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => setHoverLinkKey(null)}
        onPointerCancel={() => {
          clearLongPress()
          textLayerRef.current?.classList.remove("selecting")
          pointerRef.current = null
          setHoverLinkKey(null)
        }}
        className={`textLayer absolute inset-0 ${selectMode ? "select-mode" : ""} ${
          hoverLinkKey ? "link-hover" : ""
        }`}
        title={
          hoverLink
            ? linkHoverTitle(hoverLink)
            : selectMode
              ? "Drag or double-click to select. Long-press a word. Click a link to open it."
              : "Click to read aloud. Double-click or long-press a word to select it. Click a link to open it. Alt-click a link to read."
        }
      />
      {showOverlay && (
        <div
          data-testid="docling-overlay-container"
          className="pointer-events-none absolute inset-0 z-[3] overflow-hidden"
        >
          {overlayError ? (
            <div
              role="status"
              className="absolute top-2 right-2 max-w-[280px] rounded bg-void/90 p-2 text-xs text-rose-300 shadow border border-rose-500/30"
            >
              {overlayError}
            </div>
          ) : pageData ? (
            pageData.hasDocling ? (
              <>
                <div
                  data-testid="docling-overlay-legend"
                  className="absolute top-2 right-2 rounded bg-void/90 px-2 py-1 text-[11px] font-medium text-cream shadow border border-surface flex items-center gap-2"
                >
                  <span className="flex items-center gap-1 text-emerald-400">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    {pageData.doclingRegions.filter((regionItem) => regionItem.narrate).length} readable
                  </span>
                  <span className="text-cream-dim">•</span>
                  <span className="flex items-center gap-1 text-rose-400">
                    <span className="h-2 w-2 rounded-full bg-rose-500" />
                    {pageData.doclingRegions.filter((regionItem) => !regionItem.narrate).length} skipped
                  </span>
                </div>
                {pageData.doclingRegions.map((regionItem, regionIndex) => {
                  const [left, top, right, bottom] = regionItem.bbox
                  const width = Math.max(0, right - left)
                  const height = Math.max(0, bottom - top)
                  const isReadable = regionItem.narrate
                  return (
                    <div
                      key={regionIndex}
                      data-testid="docling-box"
                      data-status={isReadable ? "readable" : "skipped"}
                      data-narrate={String(isReadable)}
                      data-label={regionItem.label}
                      title={isReadable ? `Readable (${regionItem.label})` : `Skipped (${regionItem.label})`}
                      className={`absolute rounded border ${
                        isReadable
                          ? "border-emerald-500/80 bg-emerald-500/10 text-emerald-300"
                          : "border-rose-500/80 bg-rose-500/10 text-rose-300"
                      }`}
                      style={{
                        left: `${left * 100}%`,
                        top: `${top * 100}%`,
                        width: `${width * 100}%`,
                        height: `${height * 100}%`,
                      }}
                    />
                  )
                })}
              </>
            ) : (
              <div
                data-testid="docling-overlay-unavailable"
                className="absolute top-2 right-2 max-w-[280px] rounded bg-void/90 p-2 text-xs text-amber-300 shadow border border-amber-500/30"
              >
                Layout analysis complete. No reading regions found on this page.
              </div>
            )
          ) : null}
        </div>
      )}
      {links.length > 0 && (
        <div className="pointer-events-none absolute inset-0 z-[2]">
          {links.map((link) => (
            <span
              key={link.key}
              className={`pdf-link${hoverLinkKey === link.key ? " is-hover" : ""}`}
              style={{
                left: link.left,
                top: link.top,
                width: link.width,
                height: link.height,
              }}
            />
          ))}
        </div>
      )}
      {!rendered && (
        <div className="absolute inset-0 flex items-center justify-center bg-panel/90">
          <span className="text-xs font-semibold text-cream-dim">Page {pageNum}</span>
        </div>
      )}
    </div>
  )
}
