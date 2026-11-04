"use client"

import { useState, useEffect } from "react"
import { X, HelpCircle, Eye, RotateCw, Lightbulb, BookOpen } from "lucide-react"
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

export default function FlashcardEditor({ docId, card, onClose, onSaved }: FlashcardEditorProps) {
  const isEdit = card !== null
  const [cardType, setCardType] = useState<CardType>(
    card?.card_type === "cloze" ? "cloze" : "basic"
  )
  const [question, setQuestion] = useState(card?.question ?? "")
  const [answer, setAnswer] = useState(card?.answer ?? "")
  const [clozeText, setClozeText] = useState(card?.cloze_text ?? "")
  const [hint, setHint] = useState(card?.hint ?? "")
  const [pageNumber, setPageNumber] = useState(card?.page_number?.toString() ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showClozeHelp, setShowClozeHelp] = useState(false)
  const [previewFlipped, setPreviewFlipped] = useState(false)

  // Close on Escape in capture phase so it never leaks
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

  const insertClozeMarker = (el: HTMLTextAreaElement) => {
    const { selectionStart, selectionEnd, value } = el
    const selected = value.slice(selectionStart, selectionEnd) || "answer"
    const used = new Set(
      Array.from(value.matchAll(/\{\{c(\d+)::/g)).map((m) => parseInt(m[1], 10))
    )
    let next = 1
    while (used.has(next)) next++
    const inserted = `{{c${next}::${selected}}}`
    const nextValue = value.slice(0, selectionStart) + inserted + value.slice(selectionEnd)
    setClozeText(nextValue)
    setTimeout(() => {
      el.focus()
      el.setSelectionRange(selectionStart + 6, selectionStart + 6 + selected.length)
    }, 0)
  }

  const handleSave = async () => {
    setError(null)
    setSaving(true)
    const page = pageNumber.trim() ? parseInt(pageNumber, 10) : undefined

    try {
      if (isEdit) {
        if (card.card_type === "cloze") {
          if (!clozeText.trim()) throw new Error("Cloze text cannot be empty.")
          await updateFlashcard(card.id, { cloze_text: clozeText, hint: hint.trim() || null, page_number: page })
        } else {
          if (!question.trim() || !answer.trim()) throw new Error("Question and answer are required.")
          await updateFlashcard(card.id, { question, answer, hint: hint.trim() || null, page_number: page })
        }
      } else {
        const payload: FlashcardCreatePayload = {
          document_id: docId,
          card_type: cardType,
          page_number: page,
          hint: hint.trim() || null,
        }
        if (cardType === "cloze") {
          if (!clozeText.trim()) throw new Error("Cloze text cannot be empty.")
          payload.cloze_text = clozeText
        } else {
          if (!question.trim() || !answer.trim()) throw new Error("Question and answer are required.")
          payload.question = question
          payload.answer = answer
        }
        await createFlashcard(payload)
      }
      onSaved()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save flashcard."
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  // Live preview content
  const previewQuestion =
    cardType === "cloze"
      ? clozeText.replace(/\{\{c\d+::(.*?)(?:::.*?)?\}\}/g, "[ ... ]") || "Cloze sentence with [ ... ] blanks"
      : question.trim() || "Question will appear here..."

  const renderPreviewAnswer = () => {
    if (cardType !== "cloze") {
      return answer.trim() || "Answer will appear here..."
    }
    const text = clozeText || "Cloze sentence with [ ... ] blanks"
    const regex = /\{\{c(\d+)::(.*?)(?:::.*?)?\}\}/g
    const parts: { text: string; isHighlight: boolean }[] = []
    let lastIndex = 0
    let match
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ text: text.slice(lastIndex, match.index), isHighlight: false })
      }
      const inner = match[2].split("::")[0]
      parts.push({ text: inner, isHighlight: match[1] === "1" })
      lastIndex = match.index + match[0].length
    }
    if (lastIndex < text.length) {
      parts.push({ text: text.slice(lastIndex), isHighlight: false })
    }
    if (parts.length === 0) return <span>{text}</span>
    return (
      <span>
        {parts.map((p, i) =>
          p.isHighlight ? (
            <mark
              key={i}
              className="rounded bg-indigo/20 px-1.5 py-0.5 font-medium text-cream border border-indigo/40"
            >
              {p.text}
            </mark>
          ) : (
            <span key={i}>{p.text}</span>
          )
        )}
      </span>
    )
  }

  return (
    <div
      data-flashcard-editor="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 text-cream"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[92vh] w-full max-w-2xl flex-col rounded-xl border border-surface bg-panel shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface bg-void/50 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-indigo" />
            <h2 className="font-display text-sm font-semibold text-cream">
              {isEdit ? "Edit Flashcard" : "New Flashcard"}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Content body */}
        <div className="flex-1 space-y-4 overflow-y-auto p-5 text-xs">
          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-950/30 p-2.5 text-xs text-red-300">
              {error}
            </div>
          )}

          {/* Format selection */}
          {!isEdit && (
            <div>
              <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
                Card Format
              </label>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { id: "basic" as CardType, label: "Basic", desc: "Question & Answer" },
                  { id: "reversed" as CardType, label: "Two-way", desc: "Front & Back pair" },
                  { id: "cloze" as CardType, label: "Cloze", desc: "Fill-in-the-blank" },
                ].map((fmt) => (
                  <button
                    key={fmt.id}
                    type="button"
                    onClick={() => setCardType(fmt.id)}
                    className={`flex flex-col items-start rounded-lg border p-2.5 text-left transition-all ${
                      cardType === fmt.id
                        ? "border-indigo/60 bg-indigo/15 text-cream shadow-sm"
                        : "border-surface bg-void/50 text-cream-dim hover:border-surface hover:text-cream"
                    }`}
                  >
                    <span className="font-medium text-xs text-cream">{fmt.label}</span>
                    <span className="mt-0.5 text-[10px] text-cream-dim">{fmt.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Form inputs */}
          {cardType === "cloze" ? (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label className="text-[11px] font-medium uppercase tracking-wider text-cream-dim">
                  Cloze Text
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      const el = document.getElementById("cloze-textarea") as HTMLTextAreaElement | null
                      if (el) insertClozeMarker(el)
                    }}
                    className="rounded-md border border-indigo/40 bg-indigo/15 px-2 py-0.5 text-[11px] font-medium text-cream transition-colors hover:bg-indigo/25"
                  >
                    + Blank {"{{c1::...}}"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowClozeHelp((s) => !s)}
                    className="text-cream-dim transition-colors hover:text-cream"
                    title="How to write cloze deletion"
                  >
                    <HelpCircle className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {showClozeHelp && (
                <div className="mb-2 rounded-lg border border-surface bg-void/60 p-2.5 text-[11px] text-cream-dim leading-relaxed">
                  Wrap the words to hide in <code className="text-indigo">{"{{c1::word}}"}</code>.
                  For an optional inline clue, use <code className="text-indigo">{"{{c1::word::hint}}"}</code>.
                  Each index (<code className="text-indigo">c1</code>, <code className="text-indigo">c2</code>) generates a separate flashcard.
                </div>
              )}

              <textarea
                id="cloze-textarea"
                rows={4}
                value={clozeText}
                onChange={(e) => setClozeText(e.target.value)}
                placeholder="The mitochondria is the {{c1::powerhouse::cell organelle}} of the cell."
                className="w-full rounded-lg border border-surface bg-void p-3 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
              />
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
                  Question
                </label>
                <textarea
                  rows={3}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="e.g. What is the powerhouse of the cell?"
                  className="w-full rounded-lg border border-surface bg-void p-3 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
                  Answer
                </label>
                <textarea
                  rows={3}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  placeholder="e.g. Mitochondria"
                  className="w-full rounded-lg border border-surface bg-void p-3 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
                />
              </div>
            </div>
          )}

          {/* Hint input */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-rust">
                <Lightbulb className="h-3.5 w-3.5" />
                Hint (Optional)
              </label>
              <span className="text-[10px] text-cream-dim">Shown on demand</span>
            </div>
            <input
              type="text"
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="e.g. Think about cellular respiration or organelle functions..."
              className="w-full rounded-lg border border-surface bg-void px-3 py-2 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
            />
          </div>

          {/* Page reference */}
          <div>
            <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-cream-dim">
              Source Page Number (Optional)
            </label>
            <input
              type="number"
              min={1}
              value={pageNumber}
              onChange={(e) => setPageNumber(e.target.value)}
              placeholder="e.g. 42"
              className="w-32 rounded-lg border border-surface bg-void px-3 py-2 text-xs text-cream placeholder-cream-dim/40 outline-none transition-colors focus:border-indigo/60"
            />
          </div>

          {/* Real-time Live Card Preview */}
          <div className="rounded-xl border border-surface bg-void/40 p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[11px] font-medium text-cream-dim">
                <Eye className="h-3.5 w-3.5 text-indigo" />
                Live Card Preview ({previewFlipped ? "Answer Side" : "Question Side"})
              </span>
              <button
                type="button"
                onClick={() => setPreviewFlipped((f) => !f)}
                className="flex items-center gap-1.5 rounded-md border border-surface bg-surface/30 px-2 py-1 text-[11px] font-medium text-cream-dim transition-colors hover:text-cream"
              >
                <RotateCw className="h-3 w-3 text-indigo" />
                Flip preview
              </button>
            </div>

            <div className="flex min-h-[110px] flex-col justify-center rounded-lg border border-surface bg-panel p-4 text-center">
              <span className="mb-2 self-center rounded border border-surface bg-void px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-cream-dim">
                {previewFlipped ? "Back (Answer)" : "Front (Question)"}
              </span>
              <div className="text-sm leading-relaxed text-cream">
                {previewFlipped ? renderPreviewAnswer() : previewQuestion}
              </div>
              {!previewFlipped && hint && (
                <div className="mt-3 flex items-center justify-center gap-1.5 text-[11px] text-rust">
                  <Lightbulb className="h-3 w-3" />
                  <span>Hint: {hint}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-end gap-2.5 border-t border-surface bg-void/50 px-5 py-3.5">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg border border-surface px-3 py-1.5 text-xs font-medium text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="gradient-primary flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs font-medium text-white shadow-glow transition-all hover:opacity-95 disabled:opacity-50"
          >
            {saving ? "Saving..." : isEdit ? "Save Changes" : "Create Card"}
          </button>
        </div>
      </div>
    </div>
  )
}
