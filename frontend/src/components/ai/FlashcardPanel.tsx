"use client"

import { useCallback, useEffect, useState } from "react"
import { Layers, Loader2, RefreshCw, BookOpenCheck } from "lucide-react"
import { FlashcardStats, generateFlashcards, getFlashcardStats, getFlashcards } from "@/services/flashcardsApi"
import FlashcardManager from "./FlashcardManager"

interface FlashcardPanelProps {
  docId: number
}

export default function FlashcardPanel({ docId }: FlashcardPanelProps) {
  const [cardCount, setCardCount] = useState<number | null>(null)
  const [stats, setStats] = useState<FlashcardStats | null>(null)
  const [generating, setGenerating] = useState(false)
  const [manager, setManager] = useState<"browse" | "review" | null>(null)

  const refresh = useCallback(() => {
    getFlashcards(docId).then((cards) => setCardCount(cards.length)).catch(() => {})
    getFlashcardStats(docId).then(setStats).catch(() => {})
  }, [docId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Re-check stats/count whenever the manager overlay closes, so a card
  // created/deleted/reviewed there is reflected in this launcher immediately.
  useEffect(() => {
    if (manager === null) refresh()
  }, [manager, refresh])

  // Poll while a generation is pending (cards appear when the task finishes).
  // Baseline is the card count at click time, not 0 - manual cards survive
  // a regenerate, so "any cards at all" is not a reliable "done" signal.
  useEffect(() => {
    if (!generating) return
    const baseline = cardCount ?? 0
    const interval = setInterval(async () => {
      const fresh = await getFlashcards(docId).catch(() => null)
      if (fresh && fresh.length > baseline) {
        setCardCount(fresh.length)
        setGenerating(false)
      }
    }, 3000)
    const timeout = setTimeout(() => setGenerating(false), 300000)
    return () => {
      clearInterval(interval)
      clearTimeout(timeout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generating, docId])

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      await generateFlashcards(docId)
    } catch (err) {
      console.error("Failed to generate flashcards:", err)
      setGenerating(false)
    }
  }

  const hasCards = (cardCount ?? 0) > 0
  const dueCount = stats?.due ?? 0

  return (
    <div className="rounded-xl border border-surface bg-void/40 p-4 space-y-3.5 shadow-sm">
      <div className="flex items-center gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-md border border-emerald-500/30 bg-emerald-950/40 text-emerald-300 shadow-sm">
          <Layers className="h-3.5 w-3.5" />
        </div>
        <div>
          <h3 className="text-xs font-semibold text-cream">Flashcard Deck</h3>
          <p className="text-[10px] text-cream-dim">Active recall & spaced repetition review</p>
        </div>
      </div>

      {hasCards ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg border border-surface/60 bg-panel/60 p-2.5">
              <div className="text-lg font-bold text-cream">{cardCount ?? 0}</div>
              <div className="text-[10px] text-cream-dim">Total Cards</div>
            </div>
            <div className="rounded-lg border border-surface/60 bg-panel/60 p-2.5">
              <div
                className={`text-lg font-bold ${
                  dueCount > 0 ? "text-emerald-400" : "text-cream-dim"
                }`}
              >
                {dueCount}
              </div>
              <div className="text-[10px] text-cream-dim">Due for Review</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setManager(dueCount > 0 ? "review" : "browse")}
              className="gradient-primary flex flex-1 items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium text-white shadow-glow transition-transform hover:scale-[1.01]"
            >
              {dueCount > 0 ? (
                <>
                  <BookOpenCheck className="h-3.5 w-3.5" />
                  <span>Review ({dueCount} due)</span>
                </>
              ) : (
                <>
                  <Layers className="h-3.5 w-3.5" />
                  <span>Browse Deck</span>
                </>
              )}
            </button>
            {dueCount > 0 && (
              <button
                type="button"
                onClick={() => setManager("browse")}
                title="Browse all flashcards"
                className="flex h-8 items-center justify-center rounded-lg border border-surface bg-panel px-2.5 text-xs text-cream-dim transition-[border-color,color] duration-150 hover:border-indigo/40 hover:text-cream"
              >
                Browse
              </button>
            )}
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              title="Generate more flashcards"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-surface bg-panel text-cream-dim transition-[border-color,color] duration-150 hover:border-indigo/40 hover:text-cream disabled:opacity-40"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${generating ? "animate-spin text-indigo" : ""}`} />
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3 py-1">
          <p className="text-xs text-cream-dim">
            No flashcards yet for this document. Generate questions and answers automatically using AI.
          </p>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={generating}
            className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:opacity-60"
          >
            {generating ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Writing questions...</span>
              </>
            ) : (
              <>
                <Layers className="h-3.5 w-3.5" />
                <span>Generate flashcards</span>
              </>
            )}
          </button>
        </div>
      )}

      {manager !== null && (
        <FlashcardManager
          docId={docId}
          startInReview={manager === "review"}
          onClose={() => setManager(null)}
        />
      )}
    </div>
  )
}
