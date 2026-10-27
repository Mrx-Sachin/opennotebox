"use client"

import { useState } from "react"
import { X, HelpCircle } from "lucide-react"
import {
  CardType,
  Flashcard,
  FlashcardCreatePayload,
  createFlashcard,
  updateFlashcard,
} from "@/services/flashcardsApi"

interface FlashcardEditorProps {
  docId: number
  /** Card being edited, or null when authoring a new one. */
  card: Flashcard | null
  onClose: () => void
  onSaved: () => void
}

// A card's type can't change after creation (a "reversed" pair and a cloze
// note produce a different number of underlying rows) - so the type picker
// is only shown while creating, never while editing.
export default function FlashcardEditor({ docId, card, onClose, onSaved }: FlashcardEditorProps) {
  const isEdit = card !== null
  const [cardType, setCardType] = useState<CardType>(
    card?.card_type === "cloze" ? "cloze" : "basic"
  )
  const [question, setQuestion] = useState(card?.question ?? "")
  const [answer, setAnswer] = useState(card?.answer ?? "")
  const [clozeText, setClozeText] = useState(card?.cloze_text ?? "")
  const [pageNumber, setPageNumber] = useState(card?.page_number?.toString() ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const insertClozeMarker = (el: HTMLTextAreaElement) => {
    const { selectionStart, selectionEnd, value } = el
    const selected = value.slice(selectionStart, selectionEnd) || "answer"
    // Next available index: one past however many distinct {{cN::..}} spans
    // already exist, matching how Anki numbers additional blanks.
    const used = new Set(
      Array.from(value.matchAll(/\{\{c(\d+)::/g)).map((m) => parseInt(m[1], 10))
    )
    let next = 1
    while (used.has(next)) next++
    const inserted = `{{c${next}::${selected}}}`
    const nextValue = value.slice(0, selectionStart) + inserted + value.slice(selectionEnd)
    setClozeText(nextValue)
  }

  const handleSave = async () => {
    setError(null)
    try {
      setSaving(true)
      if (isEdit) {
        await updateFlashcard(card!.id, {
          question: cardType === "cloze" ? undefined : question,
          answer: cardType === "cloze" ? undefined : answer,
          cloze_text: cardType === "cloze" ? clozeText : undefined,
          page_number: pageNumber ? parseInt(pageNumber, 10) : undefined,
        })
      } else {
        const payload: FlashcardCreatePayload = {
          document_id: docId,
          card_type: cardType,
          page_number: pageNumber ? parseInt(pageNumber, 10) : null,
        }
        if (cardType === "cloze") {
          payload.cloze_text = clozeText
        } else {
          payload.question = question
          payload.answer = answer
        }
        await createFlashcard(payload)
      }
      onSaved()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "Failed to save card"
      setError(message)
    } finally {
      setSaving(false)
    }
  }

  const canSave =
    cardType === "cloze"
      ? clozeText.trim().length > 0
      : question.trim().length > 0 && answer.trim().length > 0

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="glass w-full max-w-lg rounded-2xl border border-surface bg-panel p-5 shadow-glow">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="font-display text-sm font-bold text-cream">
            {isEdit ? "Edit card" : "New card"}
          </h3>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {!isEdit && (
          <div className="mb-3 grid grid-cols-3 gap-2">
            {(["basic", "reversed", "cloze"] as CardType[]).map((t) => (
              <button
                key={t}
                onClick={() => setCardType(t)}
                className={`rounded-lg border px-2 py-1.5 text-xs font-medium capitalize transition-colors ${
                  cardType === t
                    ? "border-indigo/60 bg-indigo/10 text-indigo"
                    : "border-surface text-cream-dim hover:text-cream"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        )}

        {cardType === "cloze" ? (
          <div className="mb-3">
            <div className="mb-1 flex items-center justify-between">
              <label className="text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
                Text with blanks
              </label>
              <span className="flex items-center gap-1 text-[10px] text-cream-dim/70">
                <HelpCircle className="h-3 w-3" />
                Select text, click &quot;Cloze&quot; to blank it out
              </span>
            </div>
            <textarea
              value={clozeText}
              onChange={(e) => setClozeText(e.target.value)}
              onSelect={(e) => (e.currentTarget.dataset.hasSelection = "1")}
              rows={5}
              placeholder="The mitochondria is the {{c1::powerhouse}} of the cell."
              className="w-full rounded-lg border border-surface bg-void/60 p-2.5 text-sm text-cream outline-none focus:border-indigo/50"
              id="cloze-textarea"
            />
            <button
              onClick={() => {
                const el = document.getElementById("cloze-textarea") as HTMLTextAreaElement | null
                if (el) insertClozeMarker(el)
              }}
              className="mt-1.5 rounded-md border border-surface px-2 py-1 text-[11px] font-medium text-cream-dim transition-colors hover:border-indigo/40 hover:text-cream"
            >
              Mark selection as cloze
            </button>
          </div>
        ) : (
          <div className="mb-3 space-y-3">
            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
                {cardType === "reversed" ? "Front / Back" : "Question"}
              </label>
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={2}
                className="w-full rounded-lg border border-surface bg-void/60 p-2.5 text-sm text-cream outline-none focus:border-indigo/50"
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
                {cardType === "reversed" ? "Back / Front" : "Answer"}
              </label>
              <textarea
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                rows={2}
                className="w-full rounded-lg border border-surface bg-void/60 p-2.5 text-sm text-cream outline-none focus:border-indigo/50"
              />
            </div>
            {cardType === "reversed" && (
              <p className="text-[11px] text-cream-dim">
                Creates two cards: one asking front-to-back, one back-to-front.
              </p>
            )}
          </div>
        )}

        <div className="mb-4">
          <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
            Source page (optional)
          </label>
          <input
            type="number"
            min={1}
            value={pageNumber}
            onChange={(e) => setPageNumber(e.target.value)}
            className="w-24 rounded-lg border border-surface bg-void/60 p-2 text-sm text-cream outline-none focus:border-indigo/50"
          />
        </div>

        {error && <p className="mb-3 text-xs text-[#ef4444]">{error}</p>}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-surface px-3 py-1.5 text-xs font-medium text-cream-dim hover:text-cream"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            className="gradient-primary rounded-lg px-4 py-1.5 text-xs font-medium text-white shadow-glow disabled:opacity-50"
          >
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create"}
          </button>
        </div>
      </div>
    </div>
  )
}
