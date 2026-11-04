"use client"

import { useCallback, useEffect, useState } from "react"
import { Layers, Loader2, RefreshCw, BookOpenCheck, Sparkles, FolderOpen, Plus } from "lucide-react"
import {
  FlashcardStats,
  generateFlashcards,
  getFlashcardStats,
  getFlashcards,
} from "@/services/flashcardsApi"
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

  // Re-check stats and count whenever the manager overlay closes, so a card
  // created, deleted, or reviewed is reflected immediately.
  useEffect(() => {
    if (manager === null) refresh()
  }, [manager, refresh])

  // Poll while a generation is pending (cards appear when the task finishes).
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
  }, [generating, cardCount, docId])

  const handleGenerate = async () => {
    try {
      setGenerating(true)
      await generateFlashcards(docId)
    } catch {
      setGenerating(false)
    }
  }

  const dueCount = stats?.due ?? 0

  return (
    <div className="rounded-xl border border-surface bg-void/40 p-4 space-y-3.5 shadow-sm">
      <div className="mb-2.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Layers className="h-3.5 w-3.5 text-indigo" />
          <span className="font-display text-xs font-semibold text-cream">Flashcards</span>
        </div>

        {cardCount !== null && cardCount > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="rounded-md border border-surface bg-void px-2 py-0.5 text-[10px] text-cream-dim">
              {cardCount} {cardCount === 1 ? "card" : "cards"}
            </span>
            {dueCount > 0 ? (
              <span className="rounded-md border border-rust/40 bg-rust/10 px-2 py-0.5 text-[10px] font-medium text-rust">
                {dueCount} due
              </span>
            ) : (
              <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
                Caught up
              </span>
            )}
          </div>
        )}
      </div>

      {cardCount === null ? (
        <div className="flex items-center justify-center py-3 text-xs text-cream-dim">
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin text-indigo" />
          Loading flashcards...
        </div>
      ) : cardCount === 0 ? (
        <div className="space-y-2.5">
          <p className="text-xs leading-relaxed text-cream-dim">
            No flashcards yet for this document. Generate them automatically with AI or create custom cards.
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="gradient-primary flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 px-3 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95 disabled:opacity-50"
            >
              {generating ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Sparkles className="h-3.5 w-3.5" />
                  Generate Flashcards
                </>
              )}
            </button>
            <button
              onClick={() => setManager("browse")}
              title="Create card manually"
              className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
            >
              <Plus className="h-3.5 w-3.5 text-indigo" />
              Manual
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setManager("review")}
              className="gradient-primary flex flex-1 items-center justify-center gap-2 rounded-lg py-2 px-3 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95"
            >
              <BookOpenCheck className="h-3.5 w-3.5" />
              {dueCount > 0 ? `Study Cards (${dueCount} due)` : `Study Cards (${cardCount} cards)`}
            </button>
            <button
              onClick={() => setManager("browse")}
              title="Browse deck and manage cards"
              className="flex items-center gap-1.5 rounded-lg border border-surface bg-surface/30 px-3 py-2 text-xs font-medium text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream"
            >
              <FolderOpen className="h-3.5 w-3.5 text-indigo" />
              Deck
            </button>
            <button
              onClick={handleGenerate}
              disabled={generating}
              title="Generate more flashcards with AI"
              className="rounded-lg border border-surface bg-surface/30 p-2 text-cream-dim transition-colors hover:border-indigo/30 hover:bg-surface/60 hover:text-cream disabled:opacity-50"
            >
              {generating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
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
