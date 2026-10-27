"use client"

import { useCallback, useEffect, useState } from "react"
import {
  X,
  Plus,
  Pencil,
  Trash2,
  BookOpen,
  Layers,
  RotateCcw,
  Settings2,
  ChevronLeft,
  ChevronRight,
} from "lucide-react"
import {
  Flashcard,
  FlashcardQueueItem,
  FlashcardSettings,
  Rating,
  RATINGS,
  deleteFlashcard,
  getFlashcardQueue,
  getFlashcardSettings,
  getFlashcards,
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

type View = "browse" | "review" | "settings"

const TYPE_LABEL: Record<string, string> = { basic: "Basic", cloze: "Cloze" }

// Per-rating accent colors for the four grade buttons, matching the
// meaning Anki users already associate with them (red=forgot, amber=
// effortful, indigo=on-target, green=trivial).
const RATING_STYLE: Record<Rating, { border: string; text: string; bg: string }> = {
  1: { border: "border-[#ef4444]/50", text: "text-[#ef4444]", bg: "hover:bg-[#ef4444]/10" },
  2: { border: "border-rust/50", text: "text-rust", bg: "hover:bg-rust/10" },
  3: { border: "border-indigo/50", text: "text-indigo", bg: "hover:bg-indigo/10" },
  4: { border: "border-[#4ade80]/50", text: "text-[#4ade80]", bg: "hover:bg-[#4ade80]/10" },
}

export default function FlashcardManager({ docId, startInReview, onClose }: FlashcardManagerProps) {
  const [view, setView] = useState<View>(startInReview ? "review" : "browse")

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-void/95 backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-surface px-5 py-3">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-indigo" />
          <h2 className="font-display text-sm font-bold text-cream">Flashcards</h2>
        </div>
        <div className="flex items-center gap-1">
          {(["browse", "review"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`rounded-lg px-3 py-1 text-xs font-medium capitalize transition-colors ${
                view === v ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:text-cream"
              }`}
            >
              {v}
            </button>
          ))}
          <button
            onClick={() => setView("settings")}
            title="Daily limits"
            className={`rounded-lg p-1.5 transition-colors ${
              view === "settings" ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:text-cream"
            }`}
          >
            <Settings2 className="h-4 w-4" />
          </button>
          <button
            onClick={onClose}
            title="Close (Esc)"
            className="ml-2 rounded-lg p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {view === "browse" && <BrowseView docId={docId} />}
        {view === "review" && <ReviewView docId={docId} onDone={() => setView("browse")} />}
        {view === "settings" && <SettingsView />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Browse: list all cards for this document, create/edit/delete.

function BrowseView({ docId }: { docId: number }) {
  const [cards, setCards] = useState<Flashcard[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Flashcard | null | "new">(null)
  const setPage = useReader((s) => s.setPage)

  const refresh = useCallback(() => {
    setLoading(true)
    getFlashcards(docId)
      .then(setCards)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [docId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleDelete = async (card: Flashcard) => {
    const hasSiblings = cards.some((c) => c.id !== card.id && c.group_id === card.group_id)
    if (hasSiblings) {
      const deleteAll = window.confirm(
        "This card is linked to sibling cards (reversed pair or cloze note). Delete all of them? Cancel deletes only this one."
      )
      await deleteFlashcard(card.id, deleteAll)
    } else {
      await deleteFlashcard(card.id, false)
    }
    refresh()
  }

  if (loading) {
    return <p className="p-5 text-sm text-cream-dim">Loading…</p>
  }

  return (
    <div className="mx-auto max-w-2xl p-5">
      <button
        onClick={() => setEditing("new")}
        className="mb-4 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-surface py-2 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/40 hover:text-cream"
      >
        <Plus className="h-3.5 w-3.5" />
        New card
      </button>

      {cards.length === 0 ? (
        <p className="py-10 text-center text-sm text-cream-dim">
          No flashcards yet. Generate some from the document or add one by hand.
        </p>
      ) : (
        <div className="space-y-2">
          {cards.map((card) => (
            <div
              key={card.id}
              className="rounded-xl border border-surface bg-panel p-3 transition-colors hover:border-indigo/30"
            >
              <div className="mb-1.5 flex items-center gap-2 text-[10px] uppercase tracking-wide text-cream-dim">
                <span className="rounded bg-surface px-1.5 py-0.5">{TYPE_LABEL[card.card_type]}</span>
                <span className={card.source === "ai" ? "text-violet" : "text-[#4ade80]"}>
                  {card.source === "ai" ? "AI" : "Manual"}
                </span>
                {card.page_number != null && (
                  <button
                    onClick={() => setPage(card.page_number!)}
                    className="ml-auto flex items-center gap-1 text-cream-dim underline-offset-2 hover:text-indigo hover:underline"
                  >
                    <BookOpen className="h-3 w-3" />
                    Page {card.page_number}
                  </button>
                )}
              </div>
              <p className="text-sm text-cream">{card.question}</p>
              <p className="mt-1 text-xs text-cream-dim">{(card.answer || "").replace(/%%/g, "")}</p>
              <div className="mt-2 flex items-center gap-3 text-[11px] text-cream-dim/70">
                <span>{card.reps === 0 ? "New" : `${card.reps} review${card.reps === 1 ? "" : "s"}`}</span>
                {card.reps > 0 && <span>Next: {new Date(card.due).toLocaleDateString()}</span>}
                <div className="ml-auto flex gap-1">
                  <button
                    onClick={() => setEditing(card)}
                    className="rounded p-1 hover:bg-surface hover:text-cream"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => handleDelete(card)}
                    className="rounded p-1 hover:bg-surface hover:text-[#ef4444]"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <FlashcardEditor
          docId={docId}
          card={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refresh()
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Review: FSRS-graded study session.

function ReviewView({ docId, onDone }: { docId: number; onDone: () => void }) {
  const [queue, setQueue] = useState<FlashcardQueueItem[] | null>(null)
  const [index, setIndex] = useState(0)
  // Per-index reveal state, not a single flag - going back to a card you
  // already flipped should show it flipped, not force you to re-reveal it.
  const [revealedSet, setRevealedSet] = useState<Set<number>>(new Set())
  const [shownAt, setShownAt] = useState<number>(Date.now())
  // card.id -> rating already submitted this session, so a card you go back
  // to shows what you picked (and re-grading just overwrites it - FSRS
  // reschedules off whatever rating was submitted last).
  const [graded, setGraded] = useState<Record<number, Rating>>({})
  const [reviewError, setReviewError] = useState<string | null>(null)
  const setPage = useReader((s) => s.setPage)

  useEffect(() => {
    getFlashcardQueue(docId).then(setQueue).catch(() => setQueue([]))
  }, [docId])

  const card = queue?.[index]
  const revealed = revealedSet.has(index)
  const atEnd = queue !== null && index >= queue.length

  const reveal = useCallback(() => {
    setRevealedSet((s) => new Set(s).add(index))
  }, [index])

  // Free navigation, always available - this is the actual fix for "can't
  // go back and forth": the previous version only ever advanced the index
  // forward on grade, with no way back to check or change an answer.
  const goTo = useCallback(
    (next: number) => {
      setIndex(Math.max(0, next))
      setShownAt(Date.now())
    },
    []
  )

  const grade = useCallback(
    async (rating: Rating) => {
      if (!card) return
      const durationMs = Date.now() - shownAt
      setGraded((g) => ({ ...g, [card.id]: rating }))
      // Optimistic: the request fires in the background and can fail
      // silently without blocking navigation - worst case that one card's
      // schedule doesn't advance and it resurfaces sooner than it should.
      reviewFlashcard(card.id, rating, durationMs).catch((err) => {
        const msg = err?.response?.data?.detail || err?.message || "Failed to save grade"
        setReviewError(msg)
      })
      goTo(index + 1)
    },
    [card, shownAt, index, goTo]
  )

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (queue === null) return
      e.stopPropagation()
      if (e.key === "Escape") {
        onDone()
        return
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault()
        goTo(index - 1)
        return
      }
      if (e.key === "ArrowRight") {
        e.preventDefault()
        goTo(index + 1)
        return
      }
      if (!card) return
      if (!revealed) {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault()
          reveal()
        }
        return
      }
      if (["1", "2", "3", "4"].includes(e.key)) {
        e.preventDefault()
        grade(Number(e.key) as Rating)
        return
      }
      // Once revealed, Space/Enter grade "Good" (3) - the Anki convention of
      // reusing the same key to both flip the card and accept the default
      // grade, instead of leaving the session stuck until a number is hit.
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault()
        grade(3)
      }
    }
    // Capture phase so reader shortcuts (space = TTS) don't fire during review
    window.addEventListener("keydown", handler, true)
    return () => window.removeEventListener("keydown", handler, true)
  }, [queue, card, revealed, grade, goTo, index, reveal, onDone])

  if (queue === null) {
    return <p className="p-5 text-sm text-cream-dim">Loading queue…</p>
  }

  if (reviewError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-5 text-center">
        <p className="text-sm text-[#ef4444]">{reviewError}</p>
        <p className="text-xs text-cream-dim">Your grades may not have been saved. Retrying will resubmit.</p>
        <div className="mt-2 flex gap-2">
          <button
            onClick={() => {
              setReviewError(null)
              setGraded({})
              setIndex(0)
            }}
            className="gradient-primary rounded-lg px-4 py-1.5 text-xs font-medium text-white shadow-glow"
          >
            Restart session
          </button>
          <button
            onClick={() => {
              setReviewError(null)
              onDone()
            }}
            className="rounded-lg border border-surface px-3 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:text-cream"
          >
            Exit
          </button>
        </div>
      </div>
    )
  }

  if (queue.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-5 text-center">
        <p className="text-sm text-cream">Nothing due right now.</p>
        <button
          onClick={onDone}
          className="gradient-primary mt-2 rounded-lg px-4 py-1.5 text-xs font-medium text-white shadow-glow"
        >
          Back to deck
        </button>
      </div>
    )
  }

  if (atEnd) {
    const gradedCount = Object.keys(graded).length
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-5 text-center">
        <p className="text-sm text-cream">Session complete.</p>
        <p className="text-xs text-cream-dim">{gradedCount} of {queue.length} card{queue.length === 1 ? "" : "s"} graded</p>
        <div className="mt-2 flex gap-2">
          <button
            onClick={() => goTo(queue.length - 1)}
            className="flex items-center gap-1 rounded-lg border border-surface px-3 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:text-cream"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            Back to last card
          </button>
          <button
            onClick={onDone}
            className="gradient-primary rounded-lg px-4 py-1.5 text-xs font-medium text-white shadow-glow"
          >
            Back to deck
          </button>
        </div>
      </div>
    )
  }

  const myRating = card ? graded[card.id] : undefined

  return (
    <div className="flex h-full flex-col items-center p-5">
      <div className="mb-4 flex w-full max-w-xl items-center justify-between text-xs text-cream-dim">
        <span className="tabular-nums">
          {index + 1} / {queue.length}
        </span>
        <div className="flex items-center gap-3">
          {myRating != null && (
            <span
              className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${RATING_STYLE[myRating].text} ${RATING_STYLE[myRating].border} border`}
            >
              Graded: {RATINGS.find((r) => r.value === myRating)?.label}
            </span>
          )}
          {card?.page_number != null && (
            <button
              onClick={() => setPage(card.page_number!)}
              className="flex items-center gap-1 hover:text-indigo"
            >
              <BookOpen className="h-3 w-3" />
              Page {card.page_number}
            </button>
          )}
        </div>
      </div>

      <div className="flex w-full max-w-xl flex-1 items-center gap-2">
        <button
          onClick={() => goTo(index - 1)}
          disabled={index === 0}
          title="Previous (←)"
          className="rounded-full border border-surface p-2 text-cream-dim transition-colors hover:text-cream disabled:opacity-30"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        <div
          onClick={() => !revealed && reveal()}
          className={`flex min-h-[16rem] flex-1 flex-col items-center justify-center gap-4 rounded-2xl border p-8 shadow-glow ${
            revealed ? "border-indigo/40" : "cursor-pointer border-surface"
          } bg-panel`}
        >
          <span className="text-[10px] font-semibold uppercase tracking-widest text-indigo">
            {TYPE_LABEL[card!.card_type]}
          </span>
          <p className="text-center text-base leading-relaxed text-cream">{card!.question}</p>
          {revealed ? (
            <>
              <div className="my-1 h-px w-16 bg-surface" />
              <p className="whitespace-pre-wrap text-center text-sm leading-relaxed text-cream">
                {renderClozeAnswer(card!.answer)}
              </p>
            </>
          ) : (
            <span className="text-[10px] text-cream-dim">Space to reveal</span>
          )}
        </div>

        <button
          onClick={() => goTo(index + 1)}
          title="Next (→)"
          className="rounded-full border border-surface p-2 text-cream-dim transition-colors hover:text-cream"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-5 w-full max-w-xl">
        {revealed ? (
          <div className="grid grid-cols-4 gap-2">
            {RATINGS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => grade(value)}
                className={`flex flex-col items-center gap-0.5 rounded-lg border py-2 text-xs font-medium transition-colors ${RATING_STYLE[value].border} ${RATING_STYLE[value].text} ${RATING_STYLE[value].bg} ${
                  myRating === value ? "ring-1 ring-inset ring-current" : ""
                }`}
              >
                <span>{label}</span>
                <span className="text-[10px] opacity-70">
                  {card!.preview_intervals[
                    label.toLowerCase() as "again" | "hard" | "good" | "easy"
                  ] ?? ""}
                </span>
                <span className="text-[9px] opacity-40">{value}</span>
              </button>
            ))}
          </div>
        ) : (
          <button
            onClick={reveal}
            className="gradient-primary w-full rounded-lg py-2 text-xs font-medium text-white shadow-glow"
          >
            Show answer
          </button>
        )}
      </div>
    </div>
  )
}

/** The answer side wraps the target cloze span in %%...%% (see backend
 * services/cloze.py). Rendered here as a highlighted span instead of raw
 * markup so cloze cards actually look like the hidden-word reveal they are. */
function renderClozeAnswer(answer: string) {
  const parts = answer.split("%%")
  if (parts.length === 1) return answer
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="rounded bg-indigo/25 px-1 text-cream">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    )
  )
}

// ---------------------------------------------------------------------------
// Settings: daily new/review limits.

function SettingsView() {
  const [settings, setSettings] = useState<FlashcardSettings | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getFlashcardSettings().then(setSettings).catch(() => setSettings({ daily_new_limit: 20, daily_review_limit: 200 }))
  }, [])

  if (!settings) return <p className="p-5 text-sm text-cream-dim">Loading…</p>

  const save = async () => {
    setSaving(true)
    try {
      await updateFlashcardSettings(settings)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-sm p-5">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-cream">
        <RotateCcw className="h-4 w-4 text-indigo" />
        Daily limits
      </h3>
      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
            New cards per day
          </label>
          <input
            type="number"
            min={0}
            value={settings.daily_new_limit}
            onChange={(e) => setSettings({ ...settings, daily_new_limit: Number(e.target.value) })}
            className="w-full rounded-lg border border-surface bg-void/60 p-2 text-sm text-cream outline-none focus:border-indigo/50"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
            Reviews per day
          </label>
          <input
            type="number"
            min={0}
            value={settings.daily_review_limit}
            onChange={(e) => setSettings({ ...settings, daily_review_limit: Number(e.target.value) })}
            className="w-full rounded-lg border border-surface bg-void/60 p-2 text-sm text-cream outline-none focus:border-indigo/50"
          />
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="gradient-primary w-full rounded-lg py-1.5 text-xs font-medium text-white shadow-glow disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  )
}
