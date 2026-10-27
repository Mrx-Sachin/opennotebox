/**
 * Contract for PDF link hover / click. The notes selection work once
 * dropped the overlay and left only Ctrl-click, with no test to notice.
 *
 *   cd frontend && npx tsc src/lib/pdfLinks.check.ts src/lib/pdfLinks.ts \
 *     --outDir /tmp/plc --module commonjs --target es2020 \
 *     && node /tmp/plc/pdfLinks.check.js
 */
import { hitTestLink, linkHoverTitle, pdfClickIntent, PdfLinkRect } from "./pdfLinks"

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error("FAIL: " + msg)
}

const links: PdfLinkRect[] = [
  { key: "gh", left: 10, top: 10, width: 80, height: 12, url: "https://github.com/example" },
  { key: "fn", left: 200, top: 400, width: 10, height: 10, dest: "page.4" },
]

assert(hitTestLink(links, 15, 16)?.key === "gh", "hit inside github rect")
assert(hitTestLink(links, 205, 405)?.key === "fn", "hit inside footnote rect")
assert(hitTestLink(links, 0, 0) == null, "miss outside every rect")
assert(hitTestLink(links, 91, 16) == null, "miss just past the right edge")

const idle = {
  dragged: false,
  hasSelection: false,
  altKey: false,
  overLink: false,
  selectMode: false,
  detail: 1,
}

assert(pdfClickIntent({ ...idle, overLink: true }) === "follow-link", "plain click on a link follows it")
assert(pdfClickIntent({ ...idle }) === "read", "plain click on text reads")
assert(pdfClickIntent({ ...idle, altKey: true, overLink: true }) === "read", "Alt-click on a link reads")
assert(pdfClickIntent({ ...idle, dragged: true, overLink: true }) === "select", "drag across a link selects")
assert(
  pdfClickIntent({ ...idle, hasSelection: true, overLink: true }) === "select",
  "a live selection is not also a link click"
)
assert(
  pdfClickIntent({ ...idle, selectMode: true, overLink: true }) === "follow-link",
  "select-mode still follows a tap on a link"
)
assert(pdfClickIntent({ ...idle, selectMode: true }) === "ignore", "select-mode tap on text does not TTS")
assert(pdfClickIntent({ ...idle, detail: 2 }) === "select", "double-click selects a word")
assert(
  pdfClickIntent({ ...idle, selectMode: true, altKey: true, overLink: true }) === "ignore",
  "Alt-click in select-mode does not start TTS"
)

const title = linkHoverTitle(links[0])
assert(title.includes("https://github.com/example"), "hover title shows the URL")
assert(title.toLowerCase().includes("alt-click"), "hover title mentions Alt-click to read")

console.log("pdfLinks: OK")
