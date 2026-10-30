/**
 * Self-check for narrationFilter. No test framework on purpose - run it with
 * the TypeScript compiler that is already installed:
 *
 *   cd frontend && npx tsc src/lib/narrationFilter.check.ts src/lib/narrationFilter.ts \
 *     src/lib/sentences.ts src/services/types.ts --outDir /tmp/nfcheck --module commonjs --target es2020 \
 *     && node /tmp/nfcheck/lib/narrationFilter.check.js
 *
 * Prints "narrationFilter: OK" and exits 0, or throws on the first failure.
 */
import { splitSentences } from "./sentences"
import {
  buildPageSentences,
  filterNarratable,
  isPointInDoclingRegion,
  isPointInRejectedDoclingRegion,
} from "./narrationFilter"
import { DoclingRegion, LayoutRegion, NarrationExclusion } from "../services/types"

function spoken(pageText: string): string[] {
  return filterNarratable(splitSentences(pageText), pageText).map(
    (sentence) => sentence.text
  )
}

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error("FAIL: " + message)
}

// The case this was written for: an MIT Press book-pdf watermark. The URL's
// periods make splitSentences shred it into four fragments, so a per-sentence
// rule cannot see it - the whole line must be dropped by offset range.
const mit = [
  "Organizations have traditionally created value primarily through innovations.",
  "138 CHAPTER 8",
  "Downloaded from http://direct.mit.edu/books/book-pdf/2403868/book_9780262302890.pdf by guest on 07 September 2025",
  "But it shouldn't be the sum total of your approach.",
].join("\n")

const mitOut = spoken(mit)
assert(
  !mitOut.some((token) => /direct\.mit\.edu|guest on 07|^pdf by/i.test(token)),
  "MIT watermark fragments still narrated: " + JSON.stringify(mitOut)
)
assert(
  mitOut[0].startsWith("Organizations have"),
  "dropped the real opening sentence"
)
assert(
  mitOut[mitOut.length - 1].startsWith("But it shouldn't"),
  "dropped the real closing sentence"
)

// JSTOR's two-line stamp, plus a bare IP form.
assert(
  spoken("All use subject to https://about.jstor.org/terms").length === 0,
  "JSTOR terms line still narrated"
)
assert(
  spoken(
    "This content downloaded from 128.6.218.72 on Mon, 12 Jan 2026 10:04:11 UTC"
  ).length === 0,
  "JSTOR IP stamp still narrated"
)

// Conservative bias: prose that merely opens with the same words survives,
// because it carries no URL, IP or year.
const prose = "Downloaded from the shared cache, the model weights loaded instantly."
assert(spoken(prose).length === 1, "false positive on ordinary prose: " + prose)

// The pre-existing rules must still hold.
assert(spoken("42").length === 0, "bare page number leaked through")
assert(spoken("Chapter 4 .......... 57").length === 0, "TOC dot-leader leaked through")

// Index alignment is the whole reason both call sites share this function:
// the surviving sentences must keep the offsets splitSentences gave them.
const all = splitSentences(mit)
for (const sentence of filterNarratable(all, mit)) {
  assert(
    mit.slice(sentence.start, sentence.end) === sentence.text,
    "offset drift on: " + sentence.text
  )
}

// Docling region test case
const fullContent = [
  "Header: Journal of AI 2026",
  "1. Introduction to DeepRead",
  "DeepRead is a neural reading assistant for research papers. It parses documents intelligently.",
  "Figure 1: Architecture diagram showing pipeline components.",
  "Parameter | Value",
  "Batch Size | 32",
  "The experimental results demonstrate improved TTS reading.",
  "Page 1 of 10",
].join("\n")

function findOffsets(full: string, substring: string): { start: number; end: number } {
  const start = full.indexOf(substring)
  if (start === -1) throw new Error("Substring not found: " + substring)
  return { start, end: start + substring.length }
}

const headerOffsets = findOffsets(fullContent, "Header: Journal of AI 2026")
const titleOffsets = findOffsets(fullContent, "1. Introduction to DeepRead")
const body1Offsets = findOffsets(
  fullContent,
  "DeepRead is a neural reading assistant for research papers. It parses documents intelligently."
)
const captionOffsets = findOffsets(
  fullContent,
  "Figure 1: Architecture diagram showing pipeline components."
)
const tableOffsets = findOffsets(fullContent, "Parameter | Value\nBatch Size | 32")
const body2Offsets = findOffsets(
  fullContent,
  "The experimental results demonstrate improved TTS reading."
)
const footerOffsets = findOffsets(fullContent, "Page 1 of 10")

const doclingBoxes: DoclingRegion[] = [
  {
    text: "Header: Journal of AI 2026",
    label: "page_header",
    narrate: false,
    start: headerOffsets.start,
    end: headerOffsets.end,
    bbox: [0.1, 0.02, 0.9, 0.05],
    source: "docling",
  },
  {
    text: "1. Introduction to DeepRead",
    label: "section_header",
    narrate: true,
    start: titleOffsets.start,
    end: titleOffsets.end,
    bbox: [0.1, 0.08, 0.9, 0.12],
    source: "docling",
  },
  {
    text: fullContent.slice(body1Offsets.start, body1Offsets.end),
    label: "text",
    narrate: true,
    start: body1Offsets.start,
    end: body1Offsets.end,
    bbox: [0.1, 0.14, 0.9, 0.3],
    source: "docling",
  },
  {
    text: "Figure 1: Architecture diagram showing pipeline components.",
    label: "caption",
    narrate: false,
    start: captionOffsets.start,
    end: captionOffsets.end,
    bbox: [0.1, 0.32, 0.9, 0.36],
    source: "docling",
  },
  {
    text: fullContent.slice(tableOffsets.start, tableOffsets.end),
    label: "table",
    narrate: false,
    start: tableOffsets.start,
    end: tableOffsets.end,
    bbox: [0.1, 0.38, 0.9, 0.5],
    source: "docling",
  },
  {
    text: fullContent.slice(body2Offsets.start, body2Offsets.end),
    label: "text",
    narrate: true,
    start: body2Offsets.start,
    end: body2Offsets.end,
    bbox: [0.1, 0.52, 0.9, 0.6],
    source: "docling",
  },
  {
    text: "Page 1 of 10",
    label: "page_footer",
    narrate: false,
    start: footerOffsets.start,
    end: footerOffsets.end,
    bbox: [0.4, 0.95, 0.6, 0.98],
    source: "docling",
  },
]

const doclingSentences = buildPageSentences(fullContent, doclingBoxes
  .filter((region) => !region.narrate)
  .map((region) => ({ start: region.start ?? 0, end: region.end ?? 0 })))

assert(
  !doclingSentences.some((sentence) => sentence.text.includes("Journal of AI")),
  "Docling page_header was not excluded"
)
assert(
  !doclingSentences.some((sentence) => sentence.text.includes("Figure 1")),
  "Docling caption was not excluded"
)
assert(
  !doclingSentences.some((sentence) => sentence.text.includes("Batch Size")),
  "Docling table was not excluded"
)
assert(
  !doclingSentences.some((sentence) => sentence.text.includes("Page 1 of 10")),
  "Docling page_footer was not excluded"
)
assert(
  doclingSentences.some((sentence) => sentence.text.includes("Introduction to DeepRead")),
  "Docling section_header was dropped"
)
assert(
  doclingSentences.some((sentence) => sentence.text.startsWith("DeepRead is a neural")),
  "Docling body 1 sentence 1 dropped"
)
assert(
  doclingSentences.some((sentence) => sentence.text.startsWith("It parses documents")),
  "Docling body 1 sentence 2 dropped"
)
assert(
  doclingSentences.some((sentence) => sentence.text.startsWith("The experimental results")),
  "Docling body 2 sentence dropped"
)

for (const sentence of doclingSentences) {
  assert(
    fullContent.slice(sentence.start, sentence.end) === sentence.text,
    "Docling sentence offset mismatch: expected " + sentence.text
  )
}

// Zero accepted Docling regions produces empty sentences array
const allRejectedBoxes: DoclingRegion[] = [
  {
    text: "Figure 1: Architecture",
    label: "caption",
    narrate: false,
    start: 0,
    end: 22,
    bbox: [0.1, 0.1, 0.9, 0.2],
    source: "docling",
  },
]
const zeroSentences = buildPageSentences("Figure 1: Architecture", [{ start: 0, end: 22 }])
assert(zeroSentences.length === 0, "Zero accepted docling regions should produce empty sentences array")

// Legacy bounding boxes fallback
const legacyBoxes = [{ text: "Word", bbox: [10, 20, 30, 40] }]
const legacySentences = buildPageSentences(mit)
assert(legacySentences.length > 0, "Legacy boxes should fall back to legacy filterNarratable")
assert(
  !legacySentences.some((sentence) => /direct\.mit\.edu/i.test(sentence.text)),
  "Legacy fallback should still filter MIT watermark"
)

// Spatial checks: point-in-box and overlapping rejected region checks
const sampleBox: DoclingRegion = {
  text: "Caption text",
  label: "caption",
  narrate: false,
  start: 0,
  end: 12,
  bbox: [0.1, 0.2, 0.5, 0.3],
  source: "docling",
}

assert(isPointInDoclingRegion(0.2, 0.25, sampleBox), "Point inside bbox should match")
assert(!isPointInDoclingRegion(0.05, 0.25, sampleBox), "Point left of bbox should not match")
assert(!isPointInDoclingRegion(0.2, 0.35, sampleBox), "Point below bbox should not match")

// Overlap scenario: an accepted region [0.0, 0.0, 0.8, 0.8] and a rejected region [0.1, 0.2, 0.5, 0.3]
// A point at (0.2, 0.25) is inside BOTH. It must be recognized as rejected.
const overlappingRegions: DoclingRegion[] = [
  {
    text: "Surrounding accepted container",
    label: "text",
    narrate: true,
    start: 0,
    end: 30,
    bbox: [0.0, 0.0, 0.8, 0.8],
    source: "docling",
  },
  sampleBox,
]
assert(
  isPointInRejectedDoclingRegion(0.2, 0.25, overlappingRegions),
  "Point in overlapping rejected region must evaluate to rejected"
)
assert(
  !isPointInRejectedDoclingRegion(0.05, 0.05, overlappingRegions),
  "Point in only accepted region must not evaluate to rejected"
)
assert(
  !isPointInRejectedDoclingRegion(0.9, 0.9, overlappingRegions),
  "Point outside all regions must not evaluate to rejected"
)


// -------------------------------------------------------------
// New test suite: narration_exclusions UTF-16 slicing & offset verification

const sampleText = "Header Line\n\n1. Introduction\nDeepRead is awesome.\n\nTable 1: Data\nRow 1\n\nConclusion."
const headerLen = "Header Line".length
const tableStart = sampleText.indexOf("Table 1: Data")
const tableEnd = sampleText.indexOf("Conclusion.")

const exclusions: NarrationExclusion[] = [
  { start: 0, end: headerLen + 2 },
  { start: tableStart, end: tableEnd },
]

const exclusionSentences = buildPageSentences(sampleText, exclusions)
assert(exclusionSentences.length > 0, "Exclusions slicing returned no sentences")
assert(!exclusionSentences.some(s => s.text.includes("Header Line")), "Header Line should be excluded")
assert(!exclusionSentences.some(s => s.text.includes("Table 1")), "Table 1 should be excluded")
assert(exclusionSentences.some(s => s.text.includes("Introduction")), "Introduction should be included")
assert(exclusionSentences.some(s => s.text.includes("Conclusion")), "Conclusion should be included")

// Verify character offset exactness
for (const s of exclusionSentences) {
  assert(
    sampleText.slice(s.start, s.end) === s.text,
    "Offset mismatch in exclusion slice: expected \"" + s.text + "\", got \"" + sampleText.slice(s.start, s.end) + "\""
  )
}

// Excluded-only page yields empty array
const fullyExcluded: NarrationExclusion[] = [{ start: 0, end: sampleText.length }]
assert(buildPageSentences(sampleText, fullyExcluded).length === 0, "Fully excluded page should return empty array")

// Non-ASCII and UTF-16 surrogate handling
const unicodeText = "Header 🚀\n\nCafé ☕ overview: DeepRead understands UTF-16 code units perfectly."
const uHeaderLen = "Header 🚀".length // note: 🚀 is 2 UTF-16 code units
const uExclusions: NarrationExclusion[] = [{ start: 0, end: uHeaderLen + 2 }]
const uSentences = buildPageSentences(unicodeText, uExclusions)
assert(uSentences.length > 0, "Unicode sentence extraction failed")
assert(!uSentences.some(s => s.text.includes("Header")), "Unicode header should be excluded")
assert(uSentences.some(s => s.text.includes("Café")), "Unicode body with accent should be included")
for (const s of uSentences) {
  assert(
    unicodeText.slice(s.start, s.end) === s.text,
    "Unicode offset mismatch: expected \"" + s.text + "\", got \"" + unicodeText.slice(s.start, s.end) + "\""
  )
}

// Adjacent prose at boundary
const adjacentText = "EXCLUDED_PROSE Narratable text begins immediately."
const adjExclusions: NarrationExclusion[] = [{ start: 0, end: "EXCLUDED_PROSE ".length }]
const adjSentences = buildPageSentences(adjacentText, adjExclusions)
assert(adjSentences.length === 1, "Adjacent prose should yield exactly 1 sentence")
assert(adjSentences[0].text === "Narratable text begins immediately.", "Adjacent prose text mismatch")
assert(adjSentences[0].start === "EXCLUDED_PROSE ".length, "Adjacent prose start offset mismatch")
assert(adjacentText.slice(adjSentences[0].start, adjSentences[0].end) === adjSentences[0].text, "Adjacent prose offset slice mismatch")

console.log("narrationFilter: OK")
