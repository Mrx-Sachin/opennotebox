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

// ---------------------------------------------------------------------------
// Direct checks for strict citation evidence matching
// ---------------------------------------------------------------------------
import {
  findCitationEvidenceRange,
  normalizeCitationWithMap,
  normalizeCitationNeedle,
} from "./textAlign"

// 1. Ligatures and Unicode lowercasing expansion
const ligDoc = "An ef\uFB01cient system in \u0130stanbul."
const normLig = normalizeCitationWithMap(ligDoc)
assert(normLig.map.length === normLig.norm.length, "norm and map lengths stay strictly in sync")
assert(normLig.norm.includes("efficient"), "ligature fi expands to fi")
assert("\u0130".toLowerCase() !== "i", "standard Unicode lowercasing expands dotted I to i + combining dot")
assert(normLig.norm.includes("\u0130".toLowerCase()), "dot-I preserves its standard lowercase form")

const ligMatch = findCitationEvidenceRange(ligDoc, "efficient system in \u0130stanbul")
assert(ligMatch.status === "matched", "ligature and dotted capital I match symmetrically")
if (ligMatch.status === "matched") {
  assert(ligDoc.slice(ligMatch.start, ligMatch.end) === "ef\uFB01cient system in \u0130stanbul", "slice matches original indices")
}

// 2. Letter-to-letter hyphenated line-wrap and non-character artifacts
const wrapDoc = "All increases result in improved perfor\ufffemance across tasks with high-per-\nformance models."
const wrapMatch = findCitationEvidenceRange(wrapDoc, "improved performance across tasks")
assert(wrapMatch.status === "matched", "non-character and letter hyphen wrap should resolve")

// 2b. PDF.js span whitespace wrap (Whisper paper case: needle with \n matches layer with space)
const whisperLayer = "speech recogni- tion, we use an off-the-shelf architecture to avoid confound- ing our findings"
const whisperNeedle = "speech recogni-\ntion, we use an off-the-shelf architecture to avoid confound-\ning our findings"
const whisperMatch = findCitationEvidenceRange(whisperLayer, whisperNeedle)
assert(whisperMatch.status === "matched", "PDF span hyphen wrap (space) must match parser needle (newline)")
if (whisperMatch.status === "matched") {
  assert(whisperLayer.slice(whisperMatch.start, whisperMatch.end) === whisperLayer, "matched slice covers entire span")
}

// 3. Numeric signs and ranges must be preserved (not dehyphenated or stripped)
const numDoc = "Scores shifted by -1 points across 1-\n3 evaluation rounds."
const numMatchMinus1 = findCitationEvidenceRange(numDoc, "shifted by -1 points")
assert(numMatchMinus1.status === "matched", "negative sign -1 must match")

const numNearMiss = findCitationEvidenceRange(numDoc, "shifted by 1 points")
assert(numNearMiss.status === "not_found", "1 must not match -1")

const numRangeMiss = findCitationEvidenceRange(numDoc, "across 13 evaluation rounds")
assert(numRangeMiss.status === "not_found", "13 must not match 1-\\n3")

// 4. Duplicate passage must fail honestly with ambiguous
const dupDoc = "Repeated phrase here. Middle content. Repeated phrase here."
const dupMatch = findCitationEvidenceRange(dupDoc, "Repeated phrase here.")
assert(dupMatch.status === "ambiguous", "duplicate evidence must return ambiguous")

console.log("citation evidence matching: OK")
