/**
 * Alignment between the PDF.js text layer and the backend-extracted page text.
 *
 * TTS reads the backend's extracted text; clicks and highlights happen on the
 * PDF.js text-layer spans. The two differ in whitespace and minor artifacts,
 * so all matching is done on a normalized form (lowercased, whitespace
 * collapsed) with an index map back to the original string.
 */

export interface NormalizedText {
  norm: string
  /** map[i] = index in the original string of norm[i] */
  map: number[]
}

/** Soft hyphen, unicode dashes, minus, ascii hyphen. PDF line-wraps and
 * selection.toString() disagree on these, so matching treats them as space. */
function isDash(ch: string): boolean {
  const c = ch.charCodeAt(0)
  return c === 0x2d || c === 0xad || c === 0x2212 || (c >= 0x2010 && c <= 0x2015)
}
const DASHES = /[\u00ad\u2010-\u2015\u2212\-]/g

export function normalizeWithMap(original: string): NormalizedText {
  let norm = ""
  const map: number[] = []
  let pendingSpace = false
  for (let i = 0; i < original.length; i++) {
    const ch = original[i]
    if (/\s/.test(ch) || isDash(ch)) {
      pendingSpace = norm.length > 0
      continue
    }
    if (pendingSpace) {
      norm += " "
      map.push(i)
      pendingSpace = false
    }
    norm += ch.toLowerCase()
    map.push(i)
  }
  return { norm, map }
}

/** Normalize a needle the same way (no map needed). */
export function normalizeNeedle(s: string): string {
  return s.replace(DASHES, " ").replace(/\s+/g, " ").trim().toLowerCase()
}

/**
 * Find `needle` (raw text) inside a pre-normalized haystack.
 * Returns the [start, end) range in the haystack's ORIGINAL string, or null.
 * `hint` is a normalized-index near which to prefer a match (for repeated text).
 */
export function findRange(
  haystack: NormalizedText,
  needle: string,
  hint?: number
): { start: number; end: number } | null {
  const n = normalizeNeedle(needle)
  if (!n) return null
  let idx = -1
  if (hint != null && hint >= 0) {
    // Search near the hint first: closest occurrence wins
    const before = haystack.norm.lastIndexOf(n, hint)
    const after = haystack.norm.indexOf(n, Math.max(0, hint - n.length))
    if (before === -1) idx = after
    else if (after === -1) idx = before
    else idx = Math.abs(hint - before) <= Math.abs(after - hint) ? before : after
  } else {
    idx = haystack.norm.indexOf(n)
  }
  if (idx === -1) return null
  const start = haystack.map[idx]
  const endNormIdx = idx + n.length - 1
  const end = haystack.map[Math.min(endNormIdx, haystack.map.length - 1)] + 1
  return { start, end }
}

/**
 * Match a citation excerpt against the PDF text layer with progressive
 * shortening. Index-time extraction and PDF.js text-layer text often diverge
 * past the first few dozen characters (whitespace, hyphenation, soft wraps),
 * so a long needle fails even when the opening words are present.
 *
 * Tries full excerpt, then 120 / 80 / 60 / 40 / 30 char prefixes (trimmed to
 * word boundaries), then the first 3 words as a last resort.
 */
export function findRangeProgressive(
  haystack: NormalizedText,
  excerpt: string,
  hint?: number
): { start: number; end: number } | null {
  const cleaned = excerpt.replace(/\s+/g, " ").trim()
  if (!cleaned) return null

  const candidates: string[] = [cleaned]
  for (const len of [120, 80, 60, 40, 30]) {
    if (cleaned.length <= len) continue
    let cut = cleaned.slice(0, len)
    const sp = cut.lastIndexOf(" ")
    if (sp >= Math.min(20, Math.floor(len * 0.5))) cut = cut.slice(0, sp)
    if (cut.length >= 12) candidates.push(cut)
  }
  const words = cleaned.split(" ").filter(Boolean)
  if (words.length >= 3) {
    const head = words.slice(0, 3).join(" ")
    if (head.length >= 8) candidates.push(head)
  }

  const seen = new Set<string>()
  for (const needle of candidates) {
    const key = normalizeNeedle(needle)
    if (!key || seen.has(key)) continue
    seen.add(key)
    const range = findRange(haystack, needle, hint)
    if (range) return range
  }
  return null
}

interface HayToken {
  word: string
  /** inclusive start / exclusive end in haystack.norm */
  start: number
  end: number
}

function hayTokens(norm: string): HayToken[] {
  const out: HayToken[] = []
  const re = /[^\s]+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(norm))) {
    out.push({ word: m[0], start: m.index, end: m.index + m[0].length })
  }
  return out
}

function bareToken(w: string): string {
  return w.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "")
}

function tokensEqual(a: string, b: string): boolean {
  if (a === b) return true
  const na = bareToken(a)
  const nb = bareToken(b)
  return !!na && !!nb && na === nb
}

function indexOfSeq(hay: HayToken[], needle: string[], from = 0): number {
  if (!needle.length || hay.length < needle.length) return -1
  outer: for (let i = from; i <= hay.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (!tokensEqual(hay[i + j].word, needle[j])) continue outer
    }
    return i
  }
  return -1
}

function lastIndexOfSeq(hay: HayToken[], needle: string[]): number {
  if (!needle.length || hay.length < needle.length) return -1
  outer: for (let i = hay.length - needle.length; i >= 0; i--) {
    for (let j = 0; j < needle.length; j++) {
      if (!tokensEqual(hay[i + j].word, needle[j])) continue outer
    }
    return i
  }
  return -1
}

function rangeFromTokens(
  haystack: NormalizedText,
  from: HayToken,
  to: HayToken
): { start: number; end: number } {
  const start = haystack.map[from.start]
  const endNorm = Math.min(to.end - 1, haystack.map.length - 1)
  const end = haystack.map[endNorm] + 1
  return { start, end }
}

/**
 * Locate a (possibly long, multi-line, multi-page) quote on this layer.
 *
 * Prefix matching only paints the title: later words diverge (hyphens,
 * glued punctuation, text that lives on the next page). We find the first
 * short window of the quote that exists here, then walk the remaining
 * words in order so the mark runs to the last words that are actually
 * on this page.
 */
export function findQuoteExtent(
  haystack: NormalizedText,
  excerpt: string
): { start: number; end: number } | null {
  const words = normalizeNeedle(excerpt).split(" ").filter(Boolean)
  if (words.length === 0) return null

  const full = findRange(haystack, words.join(" "))
  if (full) return full

  const hay = hayTokens(haystack.norm)
  if (hay.length === 0) return null

  // First window of the quote that appears on this page (quote order).
  const win = Math.min(3, words.length)
  let startHay = -1
  let startQi = -1
  for (let i = 0; i <= words.length - win; i++) {
    const idx = indexOfSeq(hay, words.slice(i, i + win))
    if (idx >= 0) {
      startHay = idx
      startQi = i
      break
    }
  }
  if (startHay < 0) {
    // A single common word ("this", "the") is not an anchor. Two words
    // are only trusted when they are distinctive or the quote is short.
    const prefix = words.slice(0, Math.min(2, words.length))
    const distinctive = prefix.every((w) => bareToken(w).length >= 4)
    if (prefix.length && (words.length <= 4 || distinctive)) {
      const idx = indexOfSeq(hay, prefix)
      if (idx >= 0) {
        startHay = idx
        startQi = 0
      }
    }
  }
  if (startHay < 0) return null

  let lastHay = startHay + win - 1
  if (lastHay < startHay) lastHay = startHay
  let hayPos = lastHay + 1
  let qi = startQi + (lastHay - startHay + 1)
  let skips = 0
  while (qi < words.length && hayPos < hay.length) {
    const limit = Math.min(hay.length, hayPos + 20)
    let found = -1
    for (let j = hayPos; j < limit; j++) {
      if (tokensEqual(hay[j].word, words[qi])) {
        found = j
        break
      }
    }
    if (found >= 0) {
      lastHay = found
      hayPos = found + 1
      qi += 1
      skips = 0
      continue
    }
    qi += 1
    skips += 1
    if (skips >= 3) break
  }

  // If the walk stopped early (page break, hyphenation), pull the end
  // forward to the last suffix of the quote that still sits on this page.
  if (qi < words.length) {
    for (const n of [6, 5, 4, 3, 2]) {
      if (words.length < n) continue
      const idx = lastIndexOfSeq(hay, words.slice(-n))
      if (idx >= startHay) {
        lastHay = Math.max(lastHay, idx + n - 1)
        break
      }
    }
  }

  if (lastHay < startHay || lastHay >= hay.length) lastHay = startHay
  return rangeFromTokens(haystack, hay[startHay], hay[lastHay])
}
