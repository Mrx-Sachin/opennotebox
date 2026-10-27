/**
 * Self-check for narrationFilter. No test framework on purpose - run it with
 * the TypeScript compiler that is already installed:
 *
 *   cd frontend && npx tsc src/lib/narrationFilter.check.ts src/lib/narrationFilter.ts \
 *     src/lib/sentences.ts --outDir /tmp/nfcheck --module commonjs --target es2020 \
 *     && node /tmp/nfcheck/narrationFilter.check.js
 *
 * Prints "narrationFilter: OK" and exits 0, or throws on the first failure.
 */
import { splitSentences } from "./sentences"
import { filterNarratable } from "./narrationFilter"

function spoken(pageText: string): string[] {
  return filterNarratable(splitSentences(pageText), pageText).map((s) => s.text)
}

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error("FAIL: " + msg)
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
  !mitOut.some((t) => /direct\.mit\.edu|guest on 07|^pdf by/i.test(t)),
  "MIT watermark fragments still narrated: " + JSON.stringify(mitOut)
)
assert(mitOut[0].startsWith("Organizations have"), "dropped the real opening sentence")
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
  spoken("This content downloaded from 128.6.218.72 on Mon, 12 Jan 2026 10:04:11 UTC").length === 0,
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
for (const s of filterNarratable(all, mit)) {
  assert(mit.slice(s.start, s.end) === s.text, "offset drift on: " + s.text)
}

console.log("narrationFilter: OK")
