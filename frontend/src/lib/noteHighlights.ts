import { Note, NoteColor } from "@/services/types"
import { colorHex, hexToRgba } from "@/lib/noteColors"
import { findQuoteExtent, normalizeWithMap, NormalizedText } from "@/lib/textAlign"

const NOTE_HL = "note-hl"

export function clearNoteHighlights(spans: HTMLElement[]) {
  for (const span of spans) {
    span.classList.remove(NOTE_HL)
    span.style.removeProperty("--note-hl-bg")
    span.style.removeProperty("--note-hl-border")
  }
}

/** Paint highlight backgrounds onto PDF.js text-layer spans for this page. */
export function applyNoteHighlights(
  spans: HTMLElement[],
  ranges: [number, number][],
  normLayer: NormalizedText,
  notes: Note[],
  colors: NoteColor[]
) {
  clearNoteHighlights(spans)
  // Long marks first, short marks last, so a page-long wash does not hide
  // a 12-word highlight sitting on top of it.
  const ordered = [...notes].sort((a, b) => (b.quote?.length || 0) - (a.quote?.length || 0))
  for (const note of ordered) {
    if (!note.quote) continue
    const range = findQuoteExtent(normLayer, note.quote)
    if (!range) continue
    const hex = colorHex(colors, note.color)
    const bg = hexToRgba(hex, 0.26)
    ranges.forEach(([start, end], i) => {
      if (start < range.end && end > range.start) {
        const span = spans[i]
        span.classList.add(NOTE_HL)
        span.style.setProperty("--note-hl-bg", bg)
        span.style.setProperty("--note-hl-border", hex)
      }
    })
  }
}

/** Split plain page text around quote matches so the text reader can wrap them. */
export function splitHighlightedText(
  content: string,
  notes: Note[]
): { text: string; note: Note | null }[] {
  const hits: { start: number; end: number; note: Note }[] = []
  const norm = normalizeWithMap(content)
  for (const note of notes) {
    if (!note.quote) continue
    const range = findQuoteExtent(norm, note.quote)
    if (!range) continue
    hits.push({ start: range.start, end: range.end, note })
  }
  // Short marks claim first so a page-long quote does not swallow them.
  hits.sort((a, b) => a.end - a.start - (b.end - b.start) || a.start - b.start)
  type Piece = { start: number; end: number; note: Note }
  const claimed: Piece[] = []
  const pieces: Piece[] = []
  const subtract = (start: number, end: number): { start: number; end: number }[] => {
    let segs = [{ start, end }]
    for (const b of claimed) {
      const next: { start: number; end: number }[] = []
      for (const s of segs) {
        if (b.end <= s.start || b.start >= s.end) {
          next.push(s)
          continue
        }
        if (b.start > s.start) next.push({ start: s.start, end: b.start })
        if (b.end < s.end) next.push({ start: b.end, end: s.end })
      }
      segs = next
    }
    return segs.filter((s) => s.end > s.start)
  }
  for (const hit of hits) {
    for (const seg of subtract(hit.start, hit.end)) {
      pieces.push({ ...seg, note: hit.note })
    }
    claimed.push(hit)
  }
  pieces.sort((a, b) => a.start - b.start)
  if (pieces.length === 0) return [{ text: content, note: null }]
  const parts: { text: string; note: Note | null }[] = []
  let at = 0
  for (const hit of pieces) {
    if (hit.start > at) parts.push({ text: content.slice(at, hit.start), note: null })
    parts.push({ text: content.slice(hit.start, hit.end), note: hit.note })
    at = hit.end
  }
  if (at < content.length) parts.push({ text: content.slice(at), note: null })
  return parts
}

/** Short preview for cards and the selection bar. */
export function clipQuote(quote: string, max = 110): string {
  const t = quote.replace(/\s+/g, " ").trim()
  if (t.length <= max) return t
  const cut = t.slice(0, max)
  const sp = cut.lastIndexOf(" ")
  return `${sp > 40 ? cut.slice(0, sp) : cut}…`
}

export function quoteWordCount(quote: string): number {
  const t = quote.replace(/\s+/g, " ").trim()
  if (!t) return 0
  return t.split(" ").length
}
