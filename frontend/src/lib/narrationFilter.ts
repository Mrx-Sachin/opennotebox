import { Sentence, splitSentences } from "./sentences"
import {
  DoclingRegion,
  NarrationExclusion,
} from "../services/types"

// Deliberately conservative, same philosophy as the PDF column-gutter
// detection: a false positive here silently eats real content the user
// never gets to hear, which is far worse and harder to notice than a
// false negative (one page number or table row slipping through).

const PAGE_NUMBER_RE = /^(page\s+)?\d{1,4}$/i
// "Chapter 4 .......... 57" / "4.2 Results . . . . . . 12" - TOC dot leaders.
const TOC_LEADER_RE = /\.{3,}\s*\d+\s*$/
const NUMERIC_TOKEN_RE = /^[\$£€]?[\d,.]+%?$/
const REAL_WORD_RE = /[a-z]{4,}/

/** True for a numeric-table-row-shaped sentence: mostly bare numbers/currency/
 * percentages, no real prose word long enough to be a sentence on its own. */
function looksLikeTableRow(text: string): boolean {
  const tokens = text.trim().split(/\s+/)
  if (tokens.length < 4) return false
  if (REAL_WORD_RE.test(text)) return false
  const numeric = tokens.filter((t) => NUMERIC_TOKEN_RE.test(t)).length
  return numeric / tokens.length >= 0.6
}

// Publisher/library provenance stamps printed in the page margin. MIT Press,
// JSTOR, Taylor & Francis and friends all brand downloaded PDFs with a line
// like "Downloaded from http://direct.mit.edu/books/book-pdf/... by guest on
// 07 September 2025", and it lands in the extracted text of EVERY page - so
// narration reads a URL aloud, character by character, on every single page.
//
// Matched against whole LINES of page text rather than sentences, because
// splitSentences breaks on the periods inside a URL: that one stamp arrives
// as "Downloaded from http://direct." / "mit." / "edu/books/..." / "pdf by
// guest on 07 September 2025", none of which is recognisable on its own.
//
// Two conditions must both hold - a boilerplate opener AND a corroborating
// URL/IP/year - so an ordinary sentence that happens to begin "Downloaded
// from the server, the file..." is left alone. Same conservative bias as the
// rest of this file: silently eating real prose is much worse than reading
// one stray watermark.
const PROVENANCE_OPENER_RE =
  /^(?:this content\s+)?download(?:ed)?\s+(?:from|by)\b|^all use subject to\b|^brought to you by\b/i
const PROVENANCE_SIGNAL_RE =
  /https?:\/\/|www\.|\bdoi\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|\b(?:19|20)\d{2}\b/i

function isBoilerplateLine(t: string): boolean {
  if (PROVENANCE_OPENER_RE.test(t) && PROVENANCE_SIGNAL_RE.test(t)) return true
  // TOC dot leaders get shredded by splitSentences for exactly the same
  // reason the watermark does - the run of dots is a run of sentence
  // terminators - so the sentence-level TOC_LEADER_RE below can only ever
  // catch an unsplit line. Checking it here is what actually silences a
  // table of contents.
  return TOC_LEADER_RE.test(t)
}

/** Char ranges of whole boilerplate lines within the page text, in order. */
function boilerplateRanges(pageText: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = []
  let offset = 0
  for (const line of pageText.split("\n")) {
    const t = line.trim()
    if (t && isBoilerplateLine(t)) ranges.push([offset, offset + line.length])
    offset += line.length + 1 // +1 for the newline split consumed
  }
  return ranges
}

function isNarratable(text: string): boolean {
  const t = text.trim()
  if (!t) return false
  if (PAGE_NUMBER_RE.test(t)) return false
  if (TOC_LEADER_RE.test(t)) return false
  if (looksLikeTableRow(t)) return false
  return true
}

/**
 * Drop page numbers, table-of-contents dot-leader lines, bare numeric table
 * rows and publisher download watermarks from a sentence list before it
 * reaches TTS. Must be applied identically everywhere a page's sentences are
 * computed (PdfViewer's click-to-read indexing and ttsEngine's playback queue
 * both split the same page content independently) - a mismatch between the
 * two would make clicking a paragraph start reading from the wrong one.
 *
 * `pageText` must be the exact string the sentences were split from: the
 * watermark rule works on line ranges within it and matches by the
 * `start`/`end` offsets splitSentences recorded.
 */
export function filterNarratable(sentences: Sentence[], pageText: string): Sentence[] {
  const ranges = boilerplateRanges(pageText)
  return sentences.filter((s) => {
    if (!isNarratable(s.text)) return false
    return !ranges.some(([from, to]) => s.start < to && s.end > from)
  })
}

// Justified/narrow-column text - research papers especially, whose 2-column
// layout forces far more line breaks than a normal page - wraps long words
// across the break with a hyphen: "repre-\nsentations". Spoken aloud that
// comes out as two garbled fragments ("re-pray" ... "sen-TAY-shuns").
//
// This MUST NOT be done by rewriting the page text the backend extracted.
// That was tried, and it broke click-to-read across the whole app: the
// viewer matches PDF.js's independently-rendered text-layer spans against
// the backend string by literal substring search, so any edit to that
// string shifts every later sentence offset out from under the match and
// clicks silently fall back to "start from sentence 0". See the note in
// backend/app/services/pdf_parser.py:extract_reading_order_text.
//
// Instead this repairs the sentence list narration-side, and critically
// does so WITHOUT changing its length: splitSentences terminates a
// sentence at any newline run, so a wrap hyphen always lands exactly on a
// sentence boundary ("...learn high-quality repre-" | "sentations of
// speech..."). The dangling fragment is moved from the end of one sentence
// onto the front of the next, rejoining the word, and both sentences stay
// in place. Sentence count and indices are therefore identical to what
// PdfViewer computes from the same page, which is what keeps clicking a
// line and highlighting it aligned.
const WRAP_TAIL_RE = /(^|\s)([A-Za-z]{2,})-$/
const WRAP_HEAD_RE = /^[a-z]/

/**
 * Rejoin words hyphen-split across a line wrap, for speech only.
 *
 * Returns a new list of the same length and order; only `text` changes, so
 * this is safe to apply on the TTS side alone. `start`/`end` are left at
 * their original page-text offsets and are deliberately not adjusted -
 * they index the untransformed backend string, and nothing should look up
 * this list's text by those offsets.
 *
 * Occasionally merges a genuine compound word that happened to break at a
 * line end ("well-\nbeing" -> "wellbeing"). Accepted: spoken aloud the two
 * are near-indistinguishable, and true compound line breaks are far rarer
 * than plain word-wrap hyphenation.
 */
export function dehyphenateForSpeech(sentences: Sentence[]): Sentence[] {
  const out = sentences.map((s) => ({ ...s }))
  for (let i = 0; i < out.length - 1; i++) {
    const tail = WRAP_TAIL_RE.exec(out[i].text)
    if (!tail) continue
    if (!WRAP_HEAD_RE.test(out[i + 1].text)) continue
    out[i].text = out[i].text.slice(0, tail.index + tail[1].length).trimEnd()
    out[i + 1].text = tail[2] + out[i + 1].text
  }
  return out
}

export function buildPageSentences(
  content: string,
  exclusions?: NarrationExclusion[] | null
): Sentence[] {
  if (!exclusions?.length) {
    return filterNarratable(splitSentences(content), content)
  }

  const sorted = [...exclusions].sort(
    (first, second) => first.start - second.start || first.end - second.end
  )
  const sentences: Sentence[] = []
  let cursor = 0
  const appendSlice = (start: number, end: number) => {
    for (const sentence of splitSentences(content.slice(start, end))) {
      sentences.push({
        text: sentence.text,
        start: start + sentence.start,
        end: start + sentence.end,
      })
    }
  }

  for (const exclusion of sorted) {
    const start = Math.min(content.length, Math.max(0, exclusion.start))
    const end = Math.min(content.length, Math.max(start, exclusion.end))
    if (start > cursor) appendSlice(cursor, start)
    cursor = Math.max(cursor, end)
  }
  if (cursor < content.length) appendSlice(cursor, content.length)
  return filterNarratable(sentences, content)
}

export function isPointInDoclingRegion(
  normalizedX: number,
  normalizedY: number,
  region: DoclingRegion
): boolean {
  const [left, top, right, bottom] = region.bbox
  return (
    normalizedX >= left &&
    normalizedX <= right &&
    normalizedY >= top &&
    normalizedY <= bottom
  )
}

export function isPointInRejectedDoclingRegion(
  normalizedX: number,
  normalizedY: number,
  regions: DoclingRegion[]
): boolean {
  return regions.some(
    (region) => !region.narrate && isPointInDoclingRegion(normalizedX, normalizedY, region)
  )
}
