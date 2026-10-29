import type { Podcast } from "@/services/api"
import type { Citation, CitationEvidence } from "@/services/types"

export type CitationTarget = Pick<Citation, "doc_id" | "page" | "evidence">

export interface PendingCitationHighlight {
  docId: number
  page: number
  evidence?: CitationEvidence | null
}
import { create } from "zustand"

export interface ActiveReading {
  docId: number
  title: string
  numPages: number
}

export type TtsStatus = "idle" | "preparing" | "playing" | "paused"

interface TtsState {
  status: TtsStatus
  docId: number | null
  page: number | null
  sentenceIndex: number
  sentenceCount: number
  currentTime: number
  duration: number
  speed: number
  muted: boolean
}

interface ReaderState {
  selectedDocId: number | null
  currentPage: number
  numPages: number
  focusMode: boolean
  sidebarCollapsed: boolean
  /** True below the wide breakpoint: panels overlay the reader, one at a time. */
  panelsExclusive: boolean
  /** What was open before a narrow viewport collapsed everything. */
  widePanelMemory: { summaryOpen: boolean; chatOpen: boolean; sidebarCollapsed: boolean } | null
  summaryWidth: number
  aiPanelWidth: number
  summaryOpen: boolean
  chatOpen: boolean
  uploadModalOpen: boolean
  /** Auto-scroll the PDF to follow the sentence being read. Turned off when
      the user scrolls away themselves; back on via the "return" pill. */
  followReading: boolean
  /** Render PDF pages inverted (dark background, light text). */
  invertPdf: boolean
  /** Copy a text selection to the clipboard as soon as it is made.
      Off by default: selecting now opens the note bar, and copying is a
      deliberate tap (or this toggle). */
  copyOnSelect: boolean
  /** Citation excerpt waiting to be flash-highlighted once its page renders. */
  pendingHighlight: PendingCitationHighlight | null
  /** Brief UX notice when a citation jump could not flash-highlight. */
  citationToast: string | null
  /** Document whose saved reading position has been applied (or found absent).
      Progress must not be written before this: the reader starts at page 1,
      so an early save overwrites the very position it is about to restore. */
  progressRestoredFor: number | null
  /** Which side of the reader the AI Assistant (chat/podcast/flashcards) panel docks to. */
  chatSide: "left" | "right"
  tts: TtsState
  activePodcast: Podcast | null
  expandedPodcast: Podcast | null
  setActivePodcast: (podcast: Podcast | null) => void
  setExpandedPodcast: (podcast: Podcast | null) => void
  activeReading: ActiveReading | null
  expandedReading: boolean
  narrationSentences: string[]
  setActiveReading: (reading: ActiveReading | null) => void
  setExpandedReading: (expanded: boolean) => void
  setNarrationSentences: (sentences: string[]) => void

  selectDoc: (docId: number | null, numPages?: number, startPage?: number) => void
  markProgressRestored: (docId: number) => void
  setPage: (page: number) => void
  setNumPages: (n: number) => void
  toggleFocusMode: () => void
  toggleSidebar: () => void
  closePanels: () => void
  setPanelsExclusive: (exclusive: boolean) => void
  setSummaryWidth: (w: number) => void
  setAiPanelWidth: (w: number) => void
  toggleSummary: () => void
  toggleChat: () => void
  setUploadModalOpen: (open: boolean) => void
  setFollowReading: (on: boolean) => void
  toggleInvertPdf: () => void
  setCopyOnSelect: (on: boolean) => void
  jumpToCitation: (citation: CitationTarget) => void
  clearPendingHighlight: () => void
  /** Drop pending highlight and show a short toast (page jump still happened). */
  failPendingHighlight: (message: string) => void
  clearCitationToast: () => void
  toggleChatSide: () => void
  setTts: (patch: Partial<TtsState>) => void
}

const SUMMARY_WIDTH_KEY = "onb.summaryWidth"
const AI_PANEL_WIDTH_KEY = "onb.aiPanelWidth"
const AI_PANEL_WIDTH_MIN = 280
const AI_PANEL_WIDTH_MAX = 640
const INVERT_KEY = "onb.invertPdf"
const COPY_ON_SELECT_KEY = "onb.copyOnSelect"
const CHAT_SIDE_KEY = "onb.chatSide"

const initialChatSide = (): "left" | "right" => {
  if (typeof window === "undefined") return "right"
  return localStorage.getItem(CHAT_SIDE_KEY) === "left" ? "left" : "right"
}

const initialSummaryWidth = () => {
  if (typeof window === "undefined") return 380
  const saved = Number(localStorage.getItem(SUMMARY_WIDTH_KEY))
  return saved >= 320 && saved <= 480 ? saved : 380
}

const initialAiPanelWidth = () => {
  if (typeof window === "undefined") return 320
  const saved = Number(localStorage.getItem(AI_PANEL_WIDTH_KEY))
  return saved >= AI_PANEL_WIDTH_MIN && saved <= AI_PANEL_WIDTH_MAX ? saved : 320
}

const initialInvert = () => {
  if (typeof window === "undefined") return true
  return localStorage.getItem(INVERT_KEY) !== "off" // dark reading is the default
}

const initialCopyOnSelect = () => {
  if (typeof window === "undefined") return false
  return localStorage.getItem(COPY_ON_SELECT_KEY) === "on"
}

export const useReader = create<ReaderState>((set, get) => ({
  selectedDocId: null,
  currentPage: 1,
  numPages: 0,
  focusMode: false,
  sidebarCollapsed: false,
  panelsExclusive: false,
  widePanelMemory: null,
  summaryWidth: initialSummaryWidth(),
  aiPanelWidth: initialAiPanelWidth(),
  summaryOpen: true,
  chatOpen: true,
  uploadModalOpen: false,
  followReading: true,
  invertPdf: initialInvert(),
  copyOnSelect: initialCopyOnSelect(),
  pendingHighlight: null,
  citationToast: null,
  chatSide: initialChatSide(),
  progressRestoredFor: null,
  tts: {
    status: "idle",
    docId: null,
    page: null,
    sentenceIndex: 0,
    sentenceCount: 0,
    currentTime: 0,
    duration: 0,
    speed: 1,
    muted: false,
  },
  activePodcast: null,
  expandedPodcast: null,
  setActivePodcast: (podcast) => set({ activePodcast: podcast }),
  setExpandedPodcast: (podcast) => set({ expandedPodcast: podcast }),
  activeReading: null,
  expandedReading: false,
  narrationSentences: [],
  setActiveReading: (reading) => set({ activeReading: reading }),
  setExpandedReading: (expanded) => set({ expandedReading: expanded }),
  setNarrationSentences: (sentences) => set({ narrationSentences: sentences }),

  // startPage is the document's stored reading position. Defaulting to 1
  // rather than treating "no progress" as page 1 keeps "never opened" and
  // "read the first page" distinguishable everywhere else.
  selectDoc: (docId, numPages = 0, startPage) =>
    set((s) => {
      if (docId != null && s.selectedDocId === docId) {
        return {
          numPages: numPages || s.numPages,
          ...(s.panelsExclusive ? { sidebarCollapsed: true } : {}),
        }
      }
      const keepPending = docId != null && s.pendingHighlight?.docId === docId ? s.pendingHighlight : null
      const pageToOpen = keepPending ? keepPending.page : startPage && startPage > 0 ? startPage : 1
      return {
        selectedDocId: docId,
        currentPage: pageToOpen,
        progressRestoredFor: null,
        numPages,
        focusMode: false,
        pendingHighlight: keepPending,
        citationToast: keepPending ? s.citationToast : null,
        ...(s.panelsExclusive && docId != null ? { sidebarCollapsed: true } : {}),
      }
    }),
  markProgressRestored: (docId) => set({ progressRestoredFor: docId }),
  setPage: (page) => set({ currentPage: page }),
  setNumPages: (n) => set({ numPages: n }),
  toggleFocusMode: () => set((s) => ({ focusMode: !s.focusMode })),
  toggleSidebar: () =>
    set((s) => {
      const collapsed = !s.sidebarCollapsed
      if (!collapsed && s.panelsExclusive) return { sidebarCollapsed: false, summaryOpen: false, chatOpen: false }
      return { sidebarCollapsed: collapsed }
    }),
  closePanels: () => set({ summaryOpen: false, chatOpen: false, sidebarCollapsed: true }),
  // Called by the layout when the viewport class changes. Entering a narrow
  // viewport collapses everything down to the reader and remembers what was
  // open, so widening the window restores the user's real preference rather
  // than leaving them to reopen two panels by hand every time.
  setPanelsExclusive: (exclusive) =>
    set((s) => {
      if (exclusive === s.panelsExclusive) return {}
      if (exclusive) {
        return {
          panelsExclusive: true,
          widePanelMemory: {
            summaryOpen: s.summaryOpen,
            chatOpen: s.chatOpen,
            sidebarCollapsed: s.sidebarCollapsed,
          },
          summaryOpen: false,
          chatOpen: false,
          sidebarCollapsed: true,
        }
      }
      const restore = s.widePanelMemory
      return {
        panelsExclusive: false,
        ...(restore ?? {}),
        widePanelMemory: null,
      }
    }),
  setSummaryWidth: (w) => {
    const clamped = Math.min(480, Math.max(320, w))
    localStorage.setItem(SUMMARY_WIDTH_KEY, String(clamped))
    set({ summaryWidth: clamped })
  },
  setAiPanelWidth: (w) => {
    const clamped = Math.min(AI_PANEL_WIDTH_MAX, Math.max(AI_PANEL_WIDTH_MIN, w))
    localStorage.setItem(AI_PANEL_WIDTH_KEY, String(clamped))
    set({ aiPanelWidth: clamped })
  },
  // Below the wide breakpoint the side panels overlay the reader instead of
  // sitting beside it, so two open at once would stack scrims and bury the
  // page. Opening one closes the others there; on a wide screen they are real
  // columns and coexist as before.
  toggleSummary: () =>
    set((s) => {
      const open = !s.summaryOpen
      if (open && s.panelsExclusive) return { summaryOpen: true, chatOpen: false, sidebarCollapsed: true }
      return { summaryOpen: open }
    }),
  toggleChat: () =>
    set((s) => {
      const open = !s.chatOpen
      if (open && s.panelsExclusive) return { chatOpen: true, summaryOpen: false, sidebarCollapsed: true }
      return { chatOpen: open }
    }),
  setUploadModalOpen: (open) => set({ uploadModalOpen: open }),
  setFollowReading: (on) => set({ followReading: on }),
  toggleInvertPdf: () =>
    set((s) => {
      const next = !s.invertPdf
      localStorage.setItem(INVERT_KEY, next ? "on" : "off")
      return { invertPdf: next }
    }),
  setCopyOnSelect: (on) => {
    localStorage.setItem(COPY_ON_SELECT_KEY, on ? "on" : "off")
    set({ copyOnSelect: on })
  },
  jumpToCitation: (citation: CitationTarget) => {
    if (citation.page == null) {
      return set({ pendingHighlight: null, citationToast: null })
    }
    const currentDocId = get().selectedDocId
    const isSameDoc = citation.doc_id == null || citation.doc_id === currentDocId
    set({
      ...(isSameDoc ? { currentPage: citation.page } : {}),
      pendingHighlight: {
        docId: citation.doc_id ?? currentDocId ?? 0,
        page: citation.page,
        evidence: citation.evidence,
      },
      citationToast: null,
    })
  },
  clearPendingHighlight: () => set({ pendingHighlight: null }),
  failPendingHighlight: (message) =>
    set({ pendingHighlight: null, citationToast: message }),
  clearCitationToast: () => set({ citationToast: null }),
  toggleChatSide: () =>
    set((s) => {
      const next = s.chatSide === "right" ? "left" : "right"
      localStorage.setItem(CHAT_SIDE_KEY, next)
      return { chatSide: next }
    }),
  setTts: (patch) => set((s) => ({ tts: { ...s.tts, ...patch } })),
}))
