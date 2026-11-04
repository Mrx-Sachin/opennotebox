"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  X,
  Plus,
  Pencil,
  Trash2,
  Layers,
  RotateCcw,
  Settings2,
  ChevronLeft,
  ChevronRight,
  Lightbulb,
  Shuffle,
  Search,
  BookOpen,
  BookOpenCheck,
} from "lucide-react"
import {
  Flashcard,
  FlashcardQueueItem,
  FlashcardSettings,
  Rating,
  deleteFlashcard,
  getFlashcardQueue,
  getFlashcardSettings,
  getFlashcards,
  resetFlashcardDeck,
  reviewFlashcard,
  updateFlashcardSettings,
} from "@/services/flashcardsApi"
import { useReader } from "@/stores/reader"
import FlashcardEditor from "./FlashcardEditor"

interface FlashcardManagerProps {
  docId: number
  /** Opens straight into the review session instead of the browse list. */
  startInReview?: boolean
  onClose: () => void
}

type View = "review" | "browse" | "settings"

const TYPE_LABEL: Record<string, string> = { basic: "Basic", cloze: "Cloze" }

export default function FlashcardManager({ docId, startInReview, onClose }: FlashcardManagerProps) {
  const [view, setView] = useState<View>(startInReview ? "review" : "browse")

  useEffect(() => {
    const onEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
        onClose()
      }
    }
    window.addEventListener("keydown", onEscape, true)
    return () => window.removeEventListener("keydown", onEscape, true)
  }, [onClose])

  return (
    <div
      data-flashcard-manager="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-3 sm:p-6 text-cream"
      onClick={onClose}
    >
      <div
        className="relative flex h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-surface bg-panel shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top Header Bar */}
        <div className="flex items-center justify-between border-b border-surface bg-void/50 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <Layers className="h-4 w-4 text-indigo" />
            <div>
              <h2 className="font-display text-sm font-semibold text-cream">Flashcard Manager</h2>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {/* View navigation pills */}
            <div className="flex rounded-lg border border-surface bg-void p-0.5">
              <button
                onClick={() => setView("review")}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  view === "review"
                    ? "gradient-primary text-white shadow-sm"
                    : "text-cream-dim hover:text-cream"
                }`}
              >
                <BookOpenCheck className="h-3.5 w-3.5" />
                Study
              </button>
              <button
                onClick={() => setView("browse")}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  view === "browse"
                    ? "gradient-primary text-white shadow-sm"
                    : "text-cream-dim hover:text-cream"
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
                Deck
              </button>
              <button
                onClick={() => setView("settings")}
                title="Daily limits & preferences"
                className={`flex items-center rounded-md p-1.5 transition-colors ${
                  view === "settings"
                    ? "gradient-primary text-white shadow-sm"
                    : "text-cream-dim hover:text-cream"
                }`}
              >
                <Settings2 className="h-3.5 w-3.5" />
              </button>
            </div>

            <button
              onClick={onClose}
              title="Close (Esc)"
              className="ml-2 rounded-lg p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* View container */}
        <div className="min-h-0 flex-1 overflow-y-auto bg-void/20">
          {view === "review" && (
            <ReviewView docId={docId} onSwitchToBrowse={() => setView("browse")} />
          )}
          {view === "browse" && (
            <BrowseView docId={docId} onStartPractice={() => setView("review")} />
          )}
          {view === "settings" && <SettingsView />}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 1. Study / Review Session with 3D card flip & progressive hint
// ---------------------------------------------------------------------------

interface ReviewViewProps {
  docId: number
  onSwitchToBrowse: () => void
}

function ReviewView({ docId, onSwitchToBrowse }: ReviewViewProps) {
  const setPage = useReader((s) => s.setPage)

  // Queue state
  const [rawQueue, setRawQueue] = useState<FlashcardQueueItem[]>([])
  const [activeQueue, setActiveQueue] = useState<FlashcardQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [studyMode, setStudyMode] = useState<"due" | "all">("all")
  const [isShuffled, setIsShuffled] = useState(false)

  // Session navigation
  const [index, setIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [showHint, setShowHint] = useState(false)
  const [shownAt, setShownAt] = useState<number>(Date.now())

  // Session results & replay tracking
  const [masteredIds, setMasteredIds] = useState<Set<number>>(new Set())
  const [missedCards, setMissedCards] = useState<FlashcardQueueItem[]>([])
  const [isMissedPracticeRun, setIsMissedPracticeRun] = useState(false)
  const [isSessionComplete, setIsSessionComplete] = useState(false)

  // Fetch queue from API based on study mode
  const fetchQueue = useCallback(async (mode: "due" | "all", shuffleCards = false) => {
    setLoading(true)
    try {
      const items = await getFlashcardQueue(docId, 200, mode)
      setRawQueue(items)
      const list = shuffleCards ? [...items].sort(() => Math.random() - 0.5) : items
      setActiveQueue(list)
      setIndex(0)
      setFlipped(false)
      setShowHint(false)
      setShownAt(Date.now())
      setMasteredIds(new Set())
      setMissedCards([])
      setIsMissedPracticeRun(false)
      setIsSessionComplete(false)
    } catch {
      setActiveQueue([])
    } finally {
      setLoading(false)
    }
  }, [docId])

  useEffect(() => {
    fetchQueue(studyMode, isShuffled)
  }, [fetchQueue, studyMode, isShuffled])

  const currentCard: FlashcardQueueItem | undefined = activeQueue[index]

  // Intelligent progressive clue generation
  const computedHint = useMemo(() => {
    if (!currentCard) return ""
    if (currentCard.hint && currentCard.hint.trim()) {
      return currentCard.hint.trim()
    }
    const ans = currentCard.answer.replace(/%%/g, "").trim()
    if (!ans) return "No hint available for this card."

    const words = ans.split(/\s+/).filter(Boolean)
    const firstWord = words[0] || ""
    const firstLetter = firstWord.charAt(0).toUpperCase()

    if (words.length === 1) {
      return `Starts with '${firstLetter}...' (${ans.length} letters)`
    }
    return `Starts with '${firstLetter}...' (${words.length} words total)`
  }, [currentCard])

  const flipCard = useCallback(() => {
    setFlipped((f) => !f)
  }, [])

  const handleNext = useCallback(() => {
    if (index + 1 < activeQueue.length) {
      setIndex((i) => i + 1)
      setFlipped(false)
      setShowHint(false)
      setShownAt(Date.now())
    } else {
      setIsSessionComplete(true)
    }
  }, [index, activeQueue.length])

  const handlePrev = useCallback(() => {
    if (index > 0) {
      setIndex((i) => i - 1)
      setFlipped(false)
      setShowHint(false)
      setShownAt(Date.now())
    }
  }, [index])

  // Friendly human review actions
  const handleReview = useCallback((rating: Rating) => {
    if (!currentCard) return
    const duration = Date.now() - shownAt

    // Background FSRS schedule update
    reviewFlashcard(currentCard.id, rating, duration).catch(() => {})

    if (rating === 1) {
      // Missed card: add to replay queue if not already there
      setMissedCards((prev) => {
        if (!prev.some((c) => c.id === currentCard.id)) {
          return [...prev, currentCard]
        }
        return prev
      })
    } else if (rating >= 3) {
      setMasteredIds((prev) => new Set([...prev, currentCard.id]))
    }

    handleNext()
  }, [currentCard, shownAt, handleNext])

  // Replay only missed cards
  const startMissedCardsReplay = () => {
    if (missedCards.length === 0) return
    const list = [...missedCards].sort(() => Math.random() - 0.5)
    setActiveQueue(list)
    setIndex(0)
    setFlipped(false)
    setShowHint(false)
    setShownAt(Date.now())
    setMasteredIds(new Set())
    setMissedCards([])
    setIsMissedPracticeRun(true)
    setIsSessionComplete(false)
  }

  // Reset progress and study again
  const handleResetLearning = async () => {
    const ok = window.confirm(
      "Reset all flashcard learning schedules for this document? All cards will become due for new review."
    )
    if (!ok) return
    try {
      await resetFlashcardDeck(docId)
      fetchQueue(studyMode, isShuffled)
    } catch {
      alert("Failed to reset deck.")
    }
  }

  // Keyboard navigation & shortcuts (capture phase to isolate from global reader)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        document.activeElement instanceof HTMLInputElement ||
        document.activeElement instanceof HTMLTextAreaElement
      ) {
        return
      }

      if (e.key === "ArrowLeft") {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
        handlePrev()
        return
      }
      if (e.key === "ArrowRight") {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
        handleNext()
        return
      }
      if (e.key === "h" || e.key === "H") {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
        setShowHint((h) => !h)
        return
      }

      if (!flipped) {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault()
          e.stopPropagation()
          e.stopImmediatePropagation()
          flipCard()
          return
        }
      } else {
        if (e.key === "1") {
          e.preventDefault()
          e.stopPropagation()
          e.stopImmediatePropagation()
          handleReview(1)
          return
        } else if (e.key === "2") {
          e.preventDefault()
          e.stopPropagation()
          e.stopImmediatePropagation()
          handleReview(2)
          return
        } else if (e.key === "3" || e.key === " " || e.key === "Enter") {
          e.preventDefault()
          e.stopPropagation()
          e.stopImmediatePropagation()
          handleReview(3)
          return
        }
      }

      if (e.key === " " || e.key === "1" || e.key === "2" || e.key === "3") {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
      }
    }

    window.addEventListener("keydown", handler, true)
    return () => window.removeEventListener("keydown", handler, true)
  }, [flipped, flipCard, handleNext, handlePrev, handleReview])

  if (loading) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-cream-dim">
        <Layers className="h-6 w-6 animate-pulse text-indigo" />
        <p className="mt-3 text-xs">Preparing flashcards...</p>
      </div>
    )
  }

  // Empty queue screen when user has 0 cards due
  if (activeQueue.length === 0 && !isSessionComplete) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center text-cream">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-surface bg-panel text-indigo">
          <BookOpenCheck className="h-7 w-7" />
        </div>
        <h3 className="mt-4 font-display text-base font-semibold text-cream">
          {studyMode === "due" ? "All caught up on scheduled reviews!" : "No flashcards in this deck."}
        </h3>
        <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-cream-dim">
          {studyMode === "due"
            ? "You have completed all cards due for spaced repetition right now. You can do free practice on the whole deck anytime."
            : "Generate flashcards from your document summaries or add them manually in the Deck tab."}
        </p>

        <div className="mt-5 flex flex-wrap items-center justify-center gap-2.5">
          {studyMode === "due" && (
            <button
              onClick={() => {
                setStudyMode("all")
                fetchQueue("all", isShuffled)
              }}
              className="gradient-primary flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Practice all cards ({rawQueue.length} cards)
            </button>
          )}

          <button
            onClick={onSwitchToBrowse}
            className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3 py-2 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
          >
            <Layers className="h-3.5 w-3.5 text-indigo" />
            Manage deck
          </button>
        </div>
      </div>
    )
  }

  // Session complete celebration & replay arena
  if (isSessionComplete) {
    const totalDone = activeQueue.length
    const missedCount = missedCards.length
    const masteredCount = Math.max(0, totalDone - missedCount)

    return (
      <div className="flex h-full flex-col items-center justify-center p-6 text-center text-cream">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-surface bg-panel text-indigo">
          <BookOpenCheck className="h-6 w-6" />
        </div>

        <h3 className="mt-3 font-display text-lg font-bold text-cream">
          {isMissedPracticeRun ? "Missed Practice Complete!" : "Study Session Complete!"}
        </h3>
        <p className="mt-1 text-xs text-cream-dim">
          You reviewed {totalDone} {totalDone === 1 ? "card" : "cards"} in this session.
        </p>

        {/* Score badges */}
        <div className="my-5 flex items-center justify-center gap-3">
          <div className="flex flex-col items-center rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-5 py-2.5">
            <span className="font-display text-lg font-bold text-emerald-400">{masteredCount}</span>
            <span className="text-[10px] uppercase tracking-wider text-emerald-300">Got it smoothly</span>
          </div>
          {missedCount > 0 && (
            <div className="flex flex-col items-center rounded-lg border border-rust/40 bg-rust/10 px-5 py-2.5">
              <span className="font-display text-lg font-bold text-rust">{missedCount}</span>
              <span className="text-[10px] uppercase tracking-wider text-rust">Still learning</span>
            </div>
          )}
        </div>

        {/* Replay action arena */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {missedCount > 0 && (
            <button
              onClick={startMissedCardsReplay}
              className="flex items-center gap-1.5 rounded-lg border border-rust/50 bg-rust/20 px-3.5 py-2 text-xs font-semibold text-cream shadow-sm transition-all hover:bg-rust/30"
            >
              <RotateCcw className="h-3.5 w-3.5 text-rust" />
              Practice {missedCount} missed {missedCount === 1 ? "card" : "cards"}
            </button>
          )}

          <button
            onClick={() => {
              setIsMissedPracticeRun(false)
              fetchQueue(studyMode, false)
            }}
            className="gradient-primary flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Review entire deck again
          </button>

          <button
            onClick={() => {
              setIsShuffled(true)
              fetchQueue("all", true)
            }}
            className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3.5 py-2 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
          >
            <Shuffle className="h-3.5 w-3.5 text-indigo" />
            Shuffle & study again
          </button>

          <button
            onClick={handleResetLearning}
            className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3.5 py-2 text-xs font-medium text-rust transition-colors hover:border-rust/40 hover:bg-rust/10"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset deck learning progress
          </button>

          <button
            onClick={onSwitchToBrowse}
            className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3.5 py-2 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
          >
            <Layers className="h-3.5 w-3.5 text-indigo" />
            View all cards in deck
          </button>
        </div>
      </div>
    )
  }

  const progressPercent = Math.round(((index + 1) / activeQueue.length) * 100)

  return (
    <div className="flex h-full flex-col p-4 sm:p-6">
      {/* Top Study Control Bar */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-surface pb-3">
        {/* Practice Mode Selector */}
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-surface bg-void p-0.5">
            <button
              onClick={() => setStudyMode("due")}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
                studyMode === "due"
                  ? "gradient-primary text-white shadow-sm"
                  : "text-cream-dim hover:text-cream"
              }`}
            >
              ⏰ Due for Review
            </button>
            <button
              onClick={() => setStudyMode("all")}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
                studyMode === "all"
                  ? "gradient-primary text-white shadow-sm"
                  : "text-cream-dim hover:text-cream"
              }`}
            >
              🔄 Practice All ({rawQueue.length})
            </button>
          </div>

          <button
            onClick={() => {
              const next = !isShuffled
              setIsShuffled(next)
              fetchQueue(studyMode, next)
            }}
            title="Toggle shuffle order"
            className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              isShuffled
                ? "border-indigo/60 bg-indigo/20 text-cream"
                : "border-surface bg-surface/30 text-cream-dim hover:text-cream"
            }`}
          >
            <Shuffle className="h-3 w-3 text-indigo" />
            Shuffle
          </button>
        </div>

        {/* Counter & Mastery Indicators */}
        <div className="flex items-center gap-3 text-xs text-cream-dim">
          <span className="tabular-nums">
            Card <strong className="text-cream">{index + 1}</strong> of {activeQueue.length} ({progressPercent}%)
          </span>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mb-6 h-1 w-full overflow-hidden rounded-full bg-void">
        <div
          className="gradient-primary h-full transition-all duration-300"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      {/* Center 3D Flipping Card Arena */}
      <div className="flex flex-1 items-center justify-center">
        <div className="flex w-full max-w-2xl items-center gap-3">
          {/* Previous Card Arrow */}
          <button
            onClick={handlePrev}
            disabled={index === 0}
            title="Previous Card (←)"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-surface bg-surface/30 text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream disabled:opacity-20"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>

          {/* 3D Card Container */}
          <div
            className="relative min-h-[300px] w-full cursor-pointer"
            style={{ perspective: "1000px" }}
            onClick={flipCard}
          >
            <div
              className="relative h-full min-h-[300px] w-full transition-transform duration-500"
              style={{
                transformStyle: "preserve-3d",
                transform: flipped ? "rotateY(180deg)" : "rotateY(0deg)",
              }}
            >
              {/* FRONT SIDE (Question) */}
              <div
                className="absolute inset-0 flex flex-col justify-between rounded-xl border border-surface bg-panel p-6 shadow-glow"
                style={{
                  backfaceVisibility: "hidden",
                  WebkitBackfaceVisibility: "hidden",
                }}
              >
                {/* Front Top Meta */}
                <div className="flex items-center justify-between">
                  <span className="rounded-md border border-surface bg-void px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-indigo">
                    {TYPE_LABEL[currentCard.card_type] || "Card"}
                  </span>
                  <div className="flex items-center gap-2">
                    {currentCard.page_number && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setPage(currentCard.page_number!)
                        }}
                        className="flex items-center gap-1 rounded-md border border-surface bg-surface/30 px-2 py-0.5 text-[11px] text-cream-dim transition-colors hover:border-indigo/30 hover:text-cream"
                      >
                        <BookOpen className="h-3 w-3 text-indigo" />
                        Page {currentCard.page_number}
                      </button>
                    )}

                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setShowHint((h) => !h)
                      }}
                      className={`flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                        showHint
                          ? "border-rust/50 bg-rust/15 text-rust"
                          : "border-surface bg-surface/30 text-cream-dim hover:border-rust/30 hover:text-cream"
                      }`}
                      title="Toggle Hint (H)"
                    >
                      <Lightbulb className="h-3 w-3 text-rust" />
                      <span>{showHint ? "Hide hint" : "Need a hint?"}</span>
                      <kbd className="ml-0.5 rounded bg-void px-1 text-[9px] text-cream-dim">H</kbd>
                    </button>
                  </div>
                </div>

                {/* Front Question Content */}
                <div className="my-auto py-4 text-center">
                  <div className="max-h-[170px] overflow-y-auto text-base sm:text-lg font-medium leading-relaxed text-cream">
                    {currentCard.question}
                  </div>

                  {/* Warm Progressive Hint Display */}
                  {showHint && (
                    <div
                      onClick={(e) => e.stopPropagation()}
                      className="mx-auto mt-4 max-w-md rounded-lg border border-rust/40 bg-rust/10 p-3 text-left text-xs leading-relaxed text-cream"
                    >
                      <div className="flex items-center gap-1.5 font-medium text-rust mb-1">
                        <Lightbulb className="h-3.5 w-3.5" />
                        <span>Clue:</span>
                      </div>
                      <div className="text-cream-dim">{computedHint}</div>
                    </div>
                  )}
                </div>

                {/* Front Bottom Prompt */}
                <div className="flex items-center justify-center border-t border-surface pt-3 text-xs text-cream-dim">
                  <span>Click anywhere or press</span>
                  <kbd className="mx-1.5 rounded border border-surface bg-void px-1.5 py-0.5 text-[10px] font-medium text-cream shadow-sm">
                    Space
                  </kbd>
                  <span>to reveal answer</span>
                </div>
              </div>

              {/* BACK SIDE (Answer) */}
              <div
                className="absolute inset-0 flex flex-col justify-between rounded-xl border border-indigo/40 bg-panel p-6 shadow-glow"
                style={{
                  backfaceVisibility: "hidden",
                  WebkitBackfaceVisibility: "hidden",
                  transform: "rotateY(180deg)",
                }}
              >
                {/* Back Top Meta */}
                <div className="flex items-center justify-between">
                  <span className="rounded-md border border-indigo/40 bg-indigo/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-cream">
                    Answer
                  </span>
                  <div className="flex items-center gap-2">
                    {currentCard.page_number && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setPage(currentCard.page_number!)
                        }}
                        className="flex items-center gap-1 rounded-md border border-surface bg-surface/30 px-2 py-0.5 text-[11px] text-cream-dim transition-colors hover:border-indigo/30 hover:text-cream"
                      >
                        <BookOpen className="h-3 w-3 text-indigo" />
                        Page {currentCard.page_number}
                      </button>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        flipCard()
                      }}
                      className="flex items-center gap-1 rounded-md border border-surface bg-surface/30 px-2 py-0.5 text-[11px] text-cream-dim transition-colors hover:border-indigo/30 hover:text-cream"
                    >
                      <RotateCcw className="h-3 w-3 text-indigo" />
                      Flip back
                    </button>
                  </div>
                </div>

                {/* Back Answer Content */}
                <div className="my-auto py-4 text-center">
                  <div className="max-h-[170px] overflow-y-auto whitespace-pre-wrap text-base sm:text-lg font-medium leading-relaxed text-cream">
                    {currentCard.answer.includes("%%")
                      ? currentCard.answer.split("%%").map((segment, idx) =>
                          idx % 2 === 1 ? (
                            <mark
                              key={idx}
                              className="rounded bg-indigo/25 border border-indigo/40 px-1.5 py-0.5 font-semibold text-cream"
                            >
                              {segment}
                            </mark>
                          ) : (
                            <span key={idx}>{segment}</span>
                          )
                        )
                      : currentCard.answer}
                  </div>
                </div>

                {/* Back Bottom Note */}
                <div className="border-t border-surface pt-3 text-center text-[11px] text-cream-dim">
                  How well did you recall this answer?
                </div>
              </div>
            </div>
          </div>

          {/* Next Card Arrow */}
          <button
            onClick={handleNext}
            title="Next Card (→)"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-surface bg-surface/30 text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Bottom Recall Action Deck */}
      <div className="mt-5 border-t border-surface pt-4">
        {!flipped ? (
          <div className="flex justify-center">
            <button
              onClick={flipCard}
              className="gradient-primary flex items-center justify-center gap-2 rounded-lg px-8 py-2.5 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95"
            >
              <span>Reveal Answer</span>
              <kbd className="rounded bg-black/30 px-1.5 py-0.5 text-[10px] text-white">Space</kbd>
            </button>
          </div>
        ) : (
          <div className="mx-auto flex max-w-xl items-center justify-center gap-3">
            {/* 1: Still learning */}
            <button
              onClick={() => handleReview(1)}
              className="group flex flex-1 flex-col items-center justify-center rounded-lg border border-rust/40 bg-surface/30 p-2.5 transition-all hover:border-rust hover:bg-rust/10"
            >
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-xs text-rust">Still learning</span>
                <kbd className="rounded bg-void border border-surface px-1 text-[9px] text-cream-dim group-hover:text-rust">
                  1
                </kbd>
              </div>
              <span className="mt-0.5 text-[10px] text-cream-dim">Practice again</span>
            </button>

            {/* 2: Almost had it */}
            <button
              onClick={() => handleReview(2)}
              className="group flex flex-1 flex-col items-center justify-center rounded-lg border border-amber-500/40 bg-surface/30 p-2.5 transition-all hover:border-amber-500 hover:bg-amber-500/10"
            >
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-xs text-amber-300">Almost had it</span>
                <kbd className="rounded bg-void border border-surface px-1 text-[9px] text-cream-dim group-hover:text-amber-300">
                  2
                </kbd>
              </div>
              <span className="mt-0.5 text-[10px] text-cream-dim">Needs review soon</span>
            </button>

            {/* 3: Got it! */}
            <button
              onClick={() => handleReview(3)}
              className="group gradient-primary flex flex-1 flex-col items-center justify-center rounded-lg p-2.5 text-white shadow-glow transition-all hover:opacity-95"
            >
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-xs">Got it!</span>
                <kbd className="rounded bg-black/30 px-1 text-[9px] text-white">3</kbd>
              </div>
              <span className="mt-0.5 text-[10px] text-cream-dim">Mark mastered</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2. Deck / Browse View (Search, Filters, New Card, Edit, Delete, Reset Deck)
// ---------------------------------------------------------------------------

interface BrowseViewProps {
  docId: number
  onStartPractice: () => void
}

function BrowseView({ docId, onStartPractice }: BrowseViewProps) {
  const setPage = useReader((s) => s.setPage)
  const [cards, setCards] = useState<Flashcard[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  const [typeFilter, setTypeFilter] = useState<"all" | "basic" | "cloze">("all")
  const [editingCard, setEditingCard] = useState<Flashcard | null | "new">(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getFlashcards(docId)
      setCards(data)
    } finally {
      setLoading(false)
    }
  }, [docId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleDelete = async (card: Flashcard) => {
    const hasSiblings = cards.some((c) => c.id !== card.id && c.group_id === card.group_id)
    if (hasSiblings) {
      const deleteAll = window.confirm(
        "This card belongs to a card group (e.g. cloze note or two-way pair). Delete all cards in this group? Click Cancel to delete only this one."
      )
      await deleteFlashcard(card.id, deleteAll)
    } else {
      const ok = window.confirm("Delete this flashcard?")
      if (!ok) return
      await deleteFlashcard(card.id, false)
    }
    refresh()
  }

  const handleResetDeck = async () => {
    const ok = window.confirm(
      "Reset all flashcard learning schedules in this deck? All cards will start fresh as due for review."
    )
    if (!ok) return
    try {
      await resetFlashcardDeck(docId)
      refresh()
      alert("Deck learning progress reset.")
    } catch {
      alert("Failed to reset deck.")
    }
  }

  const filteredCards = cards.filter((c) => {
    if (typeFilter !== "all" && c.card_type !== typeFilter) return false
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return (
      c.question.toLowerCase().includes(q) ||
      c.answer.toLowerCase().includes(q) ||
      (c.hint && c.hint.toLowerCase().includes(q))
    )
  })

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-cream-dim">
        <Layers className="h-5 w-5 animate-pulse text-indigo" />
        <span className="ml-2 text-xs">Loading deck...</span>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-6 text-cream">
      {/* Deck Toolbar */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        {/* Search */}
        <div className="relative min-w-[200px] flex-1">
          <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-cream-dim" />
          <input
            type="text"
            placeholder="Search cards by question or answer..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-surface bg-void py-1.5 pl-8 pr-3 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
          />
        </div>

        {/* Format filters */}
        <div className="flex rounded-lg border border-surface bg-void p-0.5 text-xs">
          {(["all", "basic", "cloze"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`rounded-md px-2.5 py-1 font-medium capitalize transition-all ${
                typeFilter === t
                  ? "gradient-primary text-white shadow-sm"
                  : "text-cream-dim hover:text-cream"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Primary actions */}
        <div className="flex items-center gap-2">
          <button
            onClick={onStartPractice}
            disabled={cards.length === 0}
            className="gradient-primary flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95 disabled:opacity-40"
          >
            <BookOpenCheck className="h-3.5 w-3.5" />
            Study Deck
          </button>

          <button
            onClick={() => setEditingCard("new")}
            className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
          >
            <Plus className="h-3.5 w-3.5 text-indigo" />
            New Card
          </button>

          <button
            onClick={handleResetDeck}
            disabled={cards.length === 0}
            title="Reset scheduling for all cards in this deck"
            className="flex items-center gap-1 rounded-lg border border-surface bg-surface/30 px-2.5 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:border-rust/40 hover:bg-rust/10 hover:text-rust disabled:opacity-40"
          >
            <RotateCcw className="h-3 w-3" />
            Reset Progress
          </button>
        </div>
      </div>

      {/* Cards list */}
      {filteredCards.length === 0 ? (
        <div className="flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-surface p-6 text-center text-cream-dim">
          <Layers className="h-7 w-7 text-indigo mb-2 opacity-60" />
          <p className="text-xs font-medium text-cream">No flashcards found.</p>
          <p className="mt-1 text-[11px]">
            {search ? "No cards match your search criteria." : "Click New Card above to author one."}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredCards.map((card) => (
            <div
              key={card.id}
              className="flex items-start justify-between gap-4 rounded-xl border border-surface bg-panel p-3.5 transition-colors hover:border-indigo/30"
            >
              <div className="flex-1 min-w-0">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="rounded border border-surface bg-void px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-indigo">
                    {TYPE_LABEL[card.card_type] || "Card"}
                  </span>
                  {card.source === "ai" && (
                    <span className="rounded border border-surface bg-void px-1.5 py-0.5 text-[9px] text-cream-dim">
                      AI Generated
                    </span>
                  )}
                  {card.page_number && (
                    <button
                      onClick={() => setPage(card.page_number!)}
                      className="flex items-center gap-1 text-[11px] text-cream-dim hover:text-cream"
                    >
                      <BookOpen className="h-3 w-3 text-indigo" />
                      Page {card.page_number}
                    </button>
                  )}
                  {card.reps > 0 ? (
                    <span className="text-[10px] text-cream-dim">
                      {card.reps} {card.reps === 1 ? "review" : "reviews"}
                    </span>
                  ) : (
                    <span className="text-[10px] text-indigo">New</span>
                  )}
                </div>

                <div className="text-xs font-semibold text-cream line-clamp-2">
                  {card.question}
                </div>

                <div className="mt-1 text-xs text-cream-dim line-clamp-2">
                  {card.answer.replace(/%%/g, "")}
                </div>

                {card.hint && (
                  <div className="mt-1.5 flex items-center gap-1 text-[10px] text-rust">
                    <Lightbulb className="h-3 w-3" />
                    <span className="line-clamp-1">{card.hint}</span>
                  </div>
                )}
              </div>

              {/* Action buttons */}
              <div className="flex shrink-0 items-center gap-1">
                <button
                  onClick={() => setEditingCard(card)}
                  title="Edit card"
                  className="rounded-lg border border-surface bg-surface/30 p-1.5 text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => handleDelete(card)}
                  title="Delete card"
                  className="rounded-lg border border-surface bg-surface/30 p-1.5 text-cream-dim transition-colors hover:border-rust/40 hover:bg-rust/10 hover:text-rust"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Editor Modal */}
      {editingCard !== null && (
        <FlashcardEditor
          docId={docId}
          card={editingCard === "new" ? null : editingCard}
          onClose={() => setEditingCard(null)}
          onSaved={() => {
            setEditingCard(null)
            refresh()
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3. Settings View (Daily limits & review pacing)
// ---------------------------------------------------------------------------

function SettingsView() {
  const [settings, setSettings] = useState<FlashcardSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    getFlashcardSettings().then(setSettings).catch(() => {})
  }, [])

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    setMessage(null)
    try {
      await updateFlashcardSettings(settings)
      setMessage("Settings saved successfully!")
    } catch {
      setMessage("Failed to save settings.")
    } finally {
      setSaving(false)
    }
  }

  if (!settings) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-cream-dim">
        <Settings2 className="mr-2 h-4 w-4 animate-spin text-indigo" />
        <span className="text-xs">Loading preferences...</span>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-md p-6 text-cream">
      <h3 className="font-display text-sm font-semibold text-cream mb-1">Study Preferences</h3>
      <p className="text-xs text-cream-dim mb-5">
        Configure daily study limits for spaced repetition schedules.
      </p>

      {message && (
        <div className="mb-4 rounded-lg border border-indigo/40 bg-indigo/15 px-3 py-2 text-xs text-cream">
          {message}
        </div>
      )}

      <div className="space-y-4 rounded-xl border border-surface bg-panel p-5">
        <div>
          <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
            New cards per day
          </label>
          <input
            type="number"
            min={0}
            value={settings.daily_new_limit}
            onChange={(e) =>
              setSettings({ ...settings, daily_new_limit: Math.max(0, parseInt(e.target.value) || 0) })
            }
            className="w-full rounded-lg border border-surface bg-void px-3 py-2 text-xs text-cream outline-none transition-colors focus:border-indigo/60"
          />
          <p className="mt-1 text-[10px] text-cream-dim">
            Maximum unreviewed cards introduced in a single day.
          </p>
        </div>

        <div>
          <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
            Max reviews per day
          </label>
          <input
            type="number"
            min={0}
            value={settings.daily_review_limit}
            onChange={(e) =>
              setSettings({ ...settings, daily_review_limit: Math.max(0, parseInt(e.target.value) || 0) })
            }
            className="w-full rounded-lg border border-surface bg-void px-3 py-2 text-xs text-cream outline-none transition-colors focus:border-indigo/60"
          />
          <p className="mt-1 text-[10px] text-cream-dim">
            Maximum review repetitions scheduled across all cards in a day.
          </p>
        </div>

        <div className="pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="gradient-primary flex w-full items-center justify-center rounded-lg py-2 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save Preferences"}
          </button>
        </div>
      </div>
    </div>
  )
}
