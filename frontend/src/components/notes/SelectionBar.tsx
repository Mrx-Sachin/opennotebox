"use client"

import { useEffect, useRef, useState } from "react"
import { Copy, PenLine, X } from "lucide-react"
import { copyText } from "@/lib/clipboard"
import { useNotes } from "@/stores/notes"
import { useReader } from "@/stores/reader"
import { colorLabel } from "@/lib/noteColors"
import AddColorForm from "./AddColorForm"
import { findRange, normalizeWithMap } from "@/lib/textAlign"
import { clipQuote, quoteWordCount } from "@/lib/noteHighlights"
import { PHONE_MAX } from "@/hooks/useViewport"
import { getPage } from "@/services/api"

export default function SelectionBar() {
  const selection = useNotes((s) => s.selection)
  const setSelection = useNotes((s) => s.setSelection)
  const notes = useNotes((s) => s.notes)
  const colors = useNotes((s) => s.colors)
  const create = useNotes((s) => s.create)
  const focusNote = useNotes((s) => s.focusNote)
  const composeRequested = useNotes((s) => s.composeRequested)
  const clearCompose = useNotes((s) => s.clearCompose)
  const barRef = useRef<HTMLDivElement>(null)
  const [composing, setComposing] = useState(false)
  const [draft, setDraft] = useState("")
  const [color, setColor] = useState("amber")
  const [picking, setPicking] = useState(false)
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setComposing(false)
    setDraft("")
    setPicking(false)
    setAdding(false)
    setError(null)
    setCopied(false)
    setColor(useNotes.getState().lastColor || "amber")
    if (useNotes.getState().composeRequested) setComposing(true)
  }, [selection?.quote, selection?.page])

  useEffect(() => {
    if (composeRequested && selection) {
      setComposing(true)
      clearCompose()
    }
  }, [composeRequested, selection, clearCompose])

  useEffect(() => {
    if (!selection) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelection(null)
    }
    const onDown = (e: PointerEvent) => {
      if (barRef.current?.contains(e.target as Node)) return
      const t = e.target as HTMLElement | null
      // Phone drag-handles sit outside the bar and must not dismiss it.
      if (t?.closest?.("[data-sel-handle]")) return
      // pointerdown, not mousedown: the text layer preventDefault()s
      // pointerdown on empty space, which suppresses the compatibility
      // mouse event and left the bar stuck. A real new drag reopens
      // the bar on pointerup.
      window.getSelection()?.removeAllRanges()
      setSelection(null)
    }
    window.addEventListener("keydown", onKey)
    window.addEventListener("pointerdown", onDown)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("pointerdown", onDown)
    }
  }, [selection, setSelection])

  if (!selection) return null

  const existing = notes.find(
    (n) =>
      n.page_number === selection.page &&
      n.quote &&
      n.quote.replace(/\s+/g, " ").trim().toLowerCase() ===
        selection.quote.replace(/\s+/g, " ").trim().toLowerCase()
  )

  const words = quoteWordCount(selection.quote)
  const truncated = `“${clipQuote(selection.quote, 36)}”`
  const pageLabel =
    selection.endPage && selection.endPage !== selection.page
      ? `p.${selection.page}–${selection.endPage}`
      : null
  const sizeLabel = words > 12 ? `${words}w` : null

  const openSummary = () => {
    const reader = useReader.getState()
    if (!reader.summaryOpen) reader.toggleSummary()
  }

  const save = async (body: string | null, chosenColor: string) => {
    setSaving(true)
    setError(null)
    try {
      let wordRange: [number, number] | null = null
      let contextBefore: string | null = null
      let contextAfter: string | null = null
      try {
        const page = await getPage(selection.docId, selection.page)
        const range = findRange(normalizeWithMap(page.content || ""), selection.quote)
        if (range) {
          wordRange = [range.start, range.end]
          const text = page.content || ""
          contextBefore = text.slice(Math.max(0, range.start - 80), range.start)
          contextAfter = text.slice(range.end, range.end + 80)
        }
      } catch {
        /* server will try to fill the anchor */
      }
      const note = await create({
        document_id: selection.docId,
        page_number: selection.page,
        end_page: selection.endPage ?? null,
        quote: selection.quote,
        note: body,
        color: chosenColor,
        word_range: wordRange,
        context_before: contextBefore,
        context_after: contextAfter,
      })
      window.getSelection()?.removeAllRanges()
      openSummary()
      focusNote(note.id)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not save")
    } finally {
      setSaving(false)
    }
  }

  const phone = typeof window !== "undefined" && window.innerWidth <= PHONE_MAX
  const top = Math.min(selection.y + (phone ? 56 : 10), window.innerHeight - 180)
  const left = Math.min(Math.max(selection.x, 16), window.innerWidth - 16)

  return (
    <div
      ref={barRef}
      className="fixed z-40 -translate-x-1/2"
      style={{ top, left }}
    >
      <div className="rounded-full border border-white/10 bg-[#1a1a28]/95 px-1.5 py-1 shadow-[0_12px_40px_rgba(0,0,0,0.45),0_0_0_1px_rgba(255,255,255,0.04)] backdrop-blur-md">
        <div className="flex items-center gap-1">
          <span
            className="max-w-[148px] truncate px-2 text-[11px] text-cream-dim"
            title={selection.quote}
          >
            {pageLabel ? `${pageLabel} · ` : ""}
            {truncated}
            {sizeLabel ? ` · ${sizeLabel}` : ""}
          </span>
          {existing ? (
            <button
              type="button"
              onClick={() => {
                openSummary()
                focusNote(existing.id)
                useReader.getState().jumpToCitation(existing.page_number, existing.quote || "")
                setSelection(null)
              }}
              className="rounded-full bg-indigo/20 px-3 py-1 text-xs font-medium text-indigo hover:bg-indigo/30"
            >
              Note exists here
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={saving}
                onClick={() => setComposing(true)}
                className="flex items-center gap-1 rounded-full bg-gradient-to-br from-[#7f00ff] to-[#5b0fc8] px-3 py-1 text-xs font-medium text-white"
              >
                <PenLine className="h-3 w-3" />
                Note
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => setPicking((v) => !v)}
                className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-cream hover:bg-surface"
              >
                <span className="inline-flex gap-0.5">
                  {colors.slice(0, 3).map((c) => (
                    <span
                      key={c.id}
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: c.hex }}
                    />
                  ))}
                </span>
                Highlight
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => {
              if (copyText(selection.quote)) {
                setCopied(true)
                window.setTimeout(() => setCopied(false), 1200)
              }
            }}
            className="rounded-full px-2 py-1 text-xs font-medium text-cream-dim hover:bg-surface hover:text-cream"
            title="Copy selected text"
          >
            <span className="inline-flex items-center gap-1">
              <Copy className="h-3 w-3" />
              {copied ? "Copied" : "Copy"}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setSelection(null)}
            className="rounded-full p-1 text-cream-dim hover:text-cream"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {picking && !existing && (
        <div className="mt-2 rounded-xl border border-surface bg-[#1e1e30] px-3 py-2 shadow-glow">
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
            Highlight colour
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {colors.map((c) => (
              <button
                key={c.id}
                type="button"
                disabled={saving}
                title={colorLabel(c)}
                onClick={() => void save(null, c.id)}
                className="flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] text-cream hover:bg-surface"
              >
                <span className="h-3 w-3 rounded-full" style={{ background: c.hex }} />
                {c.name}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setAdding((v) => !v)}
              className="rounded-full px-2 py-1 text-[11px] text-cream-dim hover:text-cream"
            >
              + New
            </button>
          </div>
          {adding && (
            <AddColorForm
              onAdded={(c) => {
                setAdding(false)
                void save(null, c.id)
              }}
              onCancel={() => setAdding(false)}
            />
          )}
        </div>
      )}

      {composing && !existing && (
        <div className="mt-2 w-[min(360px,calc(100vw-24px))] rounded-xl border border-surface bg-[#1e1e30] p-3 shadow-glow">
          <p className="mb-2 line-clamp-2 text-[11px] italic text-cream-dim" title={selection.quote}>
            “{clipQuote(selection.quote, 120)}”
            {words > 22 ? ` · ${words} words` : ""}
          </p>
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Your words…"
            rows={3}
            className="w-full resize-none rounded-md border border-surface bg-void px-2 py-1.5 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {colors.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  title={colorLabel(c)}
                  onClick={() => setColor(c.id)}
                  className={`rounded-full border-2 ${
                    color === c.id ? "border-cream" : "border-transparent"
                  }`}
                >
                  <span className="block h-3 w-3 rounded-full" style={{ background: c.hex }} />
                </button>
              ))}
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setComposing(false)}
                className="rounded px-2 py-1 text-[11px] text-cream-dim hover:text-cream"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => void save(draft.trim() || null, color)}
                className="rounded bg-indigo/20 px-2 py-1 text-[11px] font-medium text-indigo hover:bg-indigo/30 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-1.5 text-center text-[11px] text-rust">{error}</p>
      )}
    </div>
  )
}
