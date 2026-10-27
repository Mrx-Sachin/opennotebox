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
    <div className="border-b border-surface bg-void/40 p-3">
      {!hasCards ? (
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-1.5 text-xs font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:opacity-60"
        >
          {generating ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              Writing questions…
            </>
          ) : (
            <>
              <Layers className="h-3 w-3" />
              Generate flashcards
            </>
          )}
        </button>
      ) : (
        <div className="flex items-center gap-2">
          <button
            onClick={() => setManager(dueCount > 0 ? "review" : "browse")}
            className="gradient-primary flex flex-1 items-center justify-center gap-2 rounded-lg py-1.5 text-xs font-medium text-white shadow-glow transition-transform hover:scale-[1.01]"
          >
            {dueCount > 0 ? (
              <>
                <BookOpenCheck className="h-3 w-3" />
                Review ({dueCount} due)
              </>
            ) : (
              <>
                <Layers className="h-3 w-3" />
                {cardCount} card{cardCount === 1 ? "" : "s"}
              </>
            )}
          </button>
          <button
            onClick={handleGenerate}
            disabled={generating}
            title="Generate more from this document"
            className="rounded-lg border border-surface p-1.5 text-cream-dim transition-colors hover:text-cream disabled:opacity-50"
          >
            {generating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
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
