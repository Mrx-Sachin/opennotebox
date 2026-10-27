/**
 * Self-check for quote-to-page highlight matching.
 *
 *   cd frontend && npx tsc src/lib/textAlign.check.ts src/lib/textAlign.ts \
 *     --outDir /tmp/tac --module commonjs --target es2020 \
 *     && node /tmp/tac/textAlign.check.js
 */
import { findQuoteExtent, normalizeWithMap } from "./textAlign"

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error("FAIL: " + msg)
}

const title = "Interpretable Context Methodology: Folder Structure as Agent Architecture"
const authors = "JAKE VAN CLIEF, DAVID MCDERMOTT, Eduba, University of Edinburgh, USA"
const body =
  "Current approaches to AI agent orchestration typically involve building multi-agent frameworks " +
  "that manage context passing, memory, error handling, and step coordination through code. " +
  "This paper presents Interpretable Context Methodology (ICM), a method that replaces framework-level " +
  "orchestration with filesystem structure."
const intro =
  "There are genuinely good agentic frameworks available today. CrewAI, LangChain, AutoGen, and others"

// PDF.js concatenates spans with a trailing space; hyphens often stay glued.
const layer = [title, authors, body, intro].join(" ")
const norm = normalizeWithMap(layer)

// Selection.toString() typically inserts a space at a line-wrap hyphen.
const longQuote = [
  title,
  authors,
  "Current approaches to AI agent orchestration typically involve building multi-agent frameworks " +
    "that manage context passing, memory, error handling, and step coordination through code. " +
    "This paper presents Interpretable Context Methodology (ICM), a method that replaces framework- " +
    "level orchestration with filesystem structure.",
  intro,
].join(" ")

const extent = findQuoteExtent(norm, longQuote)
assert(!!extent, "long quote should resolve")
assert(extent!.start <= layer.indexOf("Interpretable"), "start should be the title")
assert(
  extent!.end > layer.indexOf("available today"),
  `end should reach the last words, got ${extent!.start}-${extent!.end} of ${layer.length}`
)

const shortQuote = "There are genuinely good agentic frameworks available today"
const short = findQuoteExtent(norm, shortQuote)
assert(!!short, "short quote should resolve")
assert(short!.start >= layer.indexOf("There are genuinely"), "short start")
assert(short!.end > layer.indexOf("available today"), "short end")
assert(short!.end - short!.start < extent!.end - extent!.start, "short span smaller than long")

// Page 2 of a multi-page quote: only the later words exist here.
const page2 = normalizeWithMap("Local scripts handle the mechanical work that does not need AI at all.")
const cross = findQuoteExtent(page2, longQuote + " Local scripts handle the mechanical work that does not need AI at all.")
assert(!!cross, "page-2 remainder should still mark")
assert(
  page2.map[0] === 0 || (cross!.start >= 0 && cross!.end > cross!.start),
  "page-2 range is valid"
)

const missing = findQuoteExtent(norm, "this sentence is not on the page at all xyzzy")
assert(missing == null, "unknown quote should miss")

console.log("textAlign: OK")
