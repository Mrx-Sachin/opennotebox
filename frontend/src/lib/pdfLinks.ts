/** Hit-testing and click policy for PDF annotation links.

 * The overlay is painted for hover, but it must not steal pointer events:
 * selection and click-to-read live on the text layer. All "is this a link?"
 * answers go through these helpers so a regression cannot silently become
 * "Ctrl-click only" again.
 */

export interface PdfLinkRect {
  key: string
  left: number
  top: number
  width: number
  height: number
  url?: string
  dest?: unknown
}

export function hitTestLink(
  links: PdfLinkRect[],
  x: number,
  y: number
): PdfLinkRect | null {
  for (const link of links) {
    if (
      x >= link.left &&
      x <= link.left + link.width &&
      y >= link.top &&
      y <= link.top + link.height
    ) {
      return link
    }
  }
  return null
}

export type PdfClickIntent = "follow-link" | "read" | "select" | "ignore"

/** What a click on the PDF page should do.
 *
 * Regular click on a link follows it. Alt-click always reads (even on a
 * link). A drag or a live selection never follows a link. Select-mode
 * suppresses click-to-read but still follows a link that was not dragged.
 */
export function pdfClickIntent(opts: {
  dragged: boolean
  hasSelection: boolean
  altKey: boolean
  overLink: boolean
  selectMode: boolean
  detail: number
}): PdfClickIntent {
  if (opts.dragged || opts.hasSelection) return "select"
  if (opts.altKey) return opts.selectMode ? "ignore" : "read"
  if (opts.overLink) return "follow-link"
  if (opts.selectMode) return opts.detail > 1 ? "select" : "ignore"
  if (opts.detail > 1) return "select"
  return "read"
}

export function linkHoverTitle(link: PdfLinkRect): string {
  if (link.url) return `${link.url}\nAlt-click to read aloud instead`
  return "Go to linked page\nAlt-click to read aloud instead"
}
