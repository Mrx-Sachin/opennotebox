/** Word-level selection helpers for the PDF.js text layer.

 * Starting a drag from "near" a word used to fire click-to-read, and
 * starting from farther away selected half the paragraph. These helpers
 * let a long-press or double-click grab just the word under the pointer.
 */

export function rangeFromPoint(x: number, y: number): Range | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
  }
  if (typeof doc.caretRangeFromPoint === "function") {
    try {
      return doc.caretRangeFromPoint(x, y)
    } catch {
      return null
    }
  }
  if (typeof doc.caretPositionFromPoint === "function") {
    const pos = doc.caretPositionFromPoint(x, y)
    if (!pos) return null
    const range = document.createRange()
    try {
      range.setStart(pos.offsetNode, pos.offset)
      range.collapse(true)
      return range
    } catch {
      return null
    }
  }
  return null
}

const WORD_CHAR = /[A-Za-z0-9\u00C0-\u024F'’\-]/

export function expandRangeToWord(range: Range): boolean {
  const node = range.startContainer
  if (node.nodeType !== Node.TEXT_NODE || !node.textContent) return false
  const text = node.textContent
  let start = range.startOffset
  let end = Math.max(range.endOffset, range.startOffset)
  while (start > 0 && WORD_CHAR.test(text[start - 1])) start -= 1
  while (end < text.length && WORD_CHAR.test(text[end])) end += 1
  if (end <= start) return false
  try {
    range.setStart(node, start)
    range.setEnd(node, end)
    return true
  } catch {
    return false
  }
}

/** Select the word under (x, y). Returns the selected text, or null. */
export function selectWordAtPoint(x: number, y: number): string | null {
  const range = rangeFromPoint(x, y)
  if (!range) return null
  if (!expandRangeToWord(range)) return null
  const text = range.toString().replace(/\s+/g, " ").trim()
  if (text.length < 1) return null
  const sel = window.getSelection()
  if (!sel) return null
  sel.removeAllRanges()
  sel.addRange(range)
  return text
}

export function selectionQuote(): string {
  return (window.getSelection()?.toString() || "").replace(/\s+/g, " ").trim()
}

/** Leaf text spans only. markedContent wrappers concatenate every child and
 * would duplicate the page text (and steal clicks) if we indexed them. */
export function leafTextSpans(layer: HTMLElement): HTMLElement[] {
  return Array.from(layer.querySelectorAll("span")).filter(
    (el) =>
      !el.classList.contains("markedContent") &&
      el.childElementCount === 0 &&
      (el.textContent || "").length > 0
  )
}

/** Rotated / empty / image spans (arXiv sidebar stamp, figure OCR leftovers)
 * must not start a selection. */
export function disableNonReadingHits(layer: HTMLElement) {
  for (const el of Array.from(layer.querySelectorAll("span"))) {
    if (el.classList.contains("markedContent") || el.getAttribute("role") === "img") {
      el.classList.add("no-hit")
      continue
    }
    const transform = el.style.transform || ""
    if (/rotate\s*\(|skew/i.test(transform)) {
      el.classList.add("no-hit")
      continue
    }
    if (el.childElementCount === 0 && !(el.textContent || "").trim()) {
      el.classList.add("no-hit")
    }
  }
}

export function ensureEndOfContent(layer: HTMLElement) {
  if (layer.querySelector(".endOfContent")) return
  const end = document.createElement("div")
  end.className = "endOfContent"
  layer.appendChild(end)
}

export function isReadingSpan(el: EventTarget | null, layer: HTMLElement | null): el is HTMLElement {
  if (!layer || !(el instanceof HTMLElement)) return false
  if (el.tagName !== "SPAN") return false
  if (el.classList.contains("markedContent") || el.classList.contains("no-hit")) return false
  if (el.childElementCount > 0) return false
  return layer.contains(el)
}

export function textLayerOf(node: Node | null): HTMLElement | null {
  const el = node instanceof Element ? node : node?.parentElement
  const layer = el?.closest?.(".textLayer")
  return layer instanceof HTMLElement ? layer : null
}

/** Build a live selection between two client points. When `layer` is set,
 * both ends must sit in that layer; omit it to allow a drag across pages. */
export function setSelectionFromPoints(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  layer?: HTMLElement | null
): string | null {
  const a = rangeFromPoint(x0, y0)
  const b = rangeFromPoint(x1, y1)
  if (!a || !b) return null
  if (layer) {
    if (!layer.contains(a.startContainer) || !layer.contains(b.startContainer)) return null
  } else if (!textLayerOf(a.startContainer) || !textLayerOf(b.startContainer)) {
    return null
  }
  const range = document.createRange()
  try {
    if (a.compareBoundaryPoints(Range.START_TO_START, b) <= 0) {
      range.setStart(a.startContainer, a.startOffset)
      range.setEnd(b.startContainer, b.startOffset)
    } else {
      range.setStart(b.startContainer, b.startOffset)
      range.setEnd(a.startContainer, a.startOffset)
    }
  } catch {
    return null
  }
  const sel = window.getSelection()
  if (!sel) return null
  sel.removeAllRanges()
  sel.addRange(range)
  const text = sel.toString().replace(/\s+/g, " ").trim()
  return text || null
}

/** Client-space anchors for the start and end of the live selection.
 * Used by the phone drag handles. Desktop never reads this. */
export function selectionHandlePoints(): {
  start: { x: number; y: number }
  end: { x: number; y: number }
} | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const rects = Array.from(sel.getRangeAt(0).getClientRects()).filter(
    (r) => r.width > 0 && r.height > 0
  )
  const first = rects[0] || sel.getRangeAt(0).getBoundingClientRect()
  const last = rects[rects.length - 1] || first
  if (!first || (first.width === 0 && first.height === 0)) return null
  return {
    start: { x: first.left, y: first.bottom },
    end: { x: last.right, y: last.bottom },
  }
}

/** Move one edge of the live selection to the caret at (x, y). */
export function extendSelectionEdge(
  edge: "start" | "end",
  x: number,
  y: number
): string | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0) return null
  const live = sel.getRangeAt(0)
  const caret = rangeFromPoint(x, y)
  if (!caret) return null

  const a = document.createRange()
  const b = document.createRange()
  a.setStart(live.startContainer, live.startOffset)
  a.collapse(true)
  b.setStart(live.endContainer, live.endOffset)
  b.collapse(true)
  if (edge === "start") {
    a.setStart(caret.startContainer, caret.startOffset)
    a.collapse(true)
  } else {
    b.setStart(caret.startContainer, caret.startOffset)
    b.collapse(true)
  }

  const next = document.createRange()
  try {
    if (a.compareBoundaryPoints(Range.START_TO_START, b) <= 0) {
      next.setStart(a.startContainer, a.startOffset)
      next.setEnd(b.startContainer, b.startOffset)
    } else {
      next.setStart(b.startContainer, b.startOffset)
      next.setEnd(a.startContainer, a.startOffset)
    }
  } catch {
    return null
  }
  if (next.collapsed) return null
  sel.removeAllRanges()
  sel.addRange(next)
  const text = sel.toString().replace(/\s+/g, " ").trim()
  return text || null
}

export function pagesFromSelection(sel: Selection): { start: number; end: number } | null {
  if (!sel.rangeCount) return null
  const range = sel.getRangeAt(0)
  const pageOf = (node: Node | null): number | null => {
    const el = node instanceof Element ? node : node?.parentElement
    const host = el?.closest?.('[id^="pdf-page-container-"], [id^="text-page-container-"]')
    if (!host) return null
    const n = Number(host.id.replace(/^(?:pdf|text)-page-container-/, ""))
    return Number.isFinite(n) && n > 0 ? n : null
  }
  const a = pageOf(range.startContainer)
  const b = pageOf(range.endContainer)
  if (a == null || b == null) return null
  return { start: Math.min(a, b), end: Math.max(a, b) }
}
