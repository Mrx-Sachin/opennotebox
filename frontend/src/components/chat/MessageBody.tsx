"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { visit } from "unist-util-visit"
import type { Root, Text as MdastText } from "mdast"
import type { PluggableList } from "unified"
import { Citation } from "@/services/types"
import { ExternalLink } from "lucide-react"

/** Matches inline citation markers like [1], [2, 3] emitted by the chat engine. */
const MARKER_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g

interface MessageBodyProps {
  content: string
  citations?: Citation[]
  onJump: (citation: Citation) => void
}

interface Preview {
  citation: Citation
  /** Chip bounding rect in viewport coordinates. */
  rect: DOMRect
}

const CARD_WIDTH = 320
const CARD_MARGIN = 8

/**
 * Splits plain text on resolvable [n] / [n, m] citation markers.
 * Returns alternating text/marker nodes so both the markdown renderer and
 * the plain-text fallback can share the exact same substitution logic.
 */
type TextSegment = { type: "text"; value: string } | { type: "marker"; numbers: number[] }

function splitCitationMarkers(text: string, resolvable: Set<number>): TextSegment[] {
  const segments: TextSegment[] = []
  let lastIndex = 0
  for (const match of text.matchAll(MARKER_RE)) {
    const numbers = match[1]
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => resolvable.has(n))
    if (numbers.length === 0) continue

    if (match.index! > lastIndex) {
      segments.push({ type: "text", value: text.slice(lastIndex, match.index) })
    }
    segments.push({ type: "marker", numbers })
    lastIndex = match.index! + match[0].length
  }
  if (lastIndex < text.length) {
    segments.push({ type: "text", value: text.slice(lastIndex) })
  }
  return segments
}

/** Remark plugin: rewrites mdast text nodes containing resolvable citation
    markers into a mix of text nodes and custom `citationMarker` nodes, so
    ReactMarkdown can render them as chips via a `components` override. */
function remarkCitationMarkers(resolvable: Set<number>) {
  return (tree: Root) => {
    visit(tree, "text", (node: MdastText, index, parent) => {
      if (index == null || !parent || resolvable.size === 0) return
      const segments = splitCitationMarkers(node.value, resolvable)
      if (segments.length <= 1 && segments[0]?.type === "text") return

      const newNodes = segments.map((seg) =>
        seg.type === "text"
          ? { type: "text", value: seg.value }
          : {
              type: "citationMarker",
              data: { hName: "citation-marker", hProperties: { numbers: seg.numbers.join(",") } },
              children: [],
            }
      )
      parent.children.splice(index, 1, ...(newNodes as never[]))
      return index + newNodes.length
    })
  }
}

export default function MessageBody({ content, citations, onJump }: MessageBodyProps) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const byNumber = new Map<number, Citation>()
  for (const c of citations ?? []) byNumber.set(c.n, c)

  const scheduleHide = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current)
    hideTimer.current = setTimeout(() => setPreview(null), 150)
  }, [])

  const cancelHide = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current)
  }, [])

  useEffect(() => () => cancelHide(), [cancelHide])

  const renderChip = (numbers: number[], key: string) => (
    <span key={key}>
      {numbers.map((n) => {
        const citation = byNumber.get(n)
        if (!citation) return null
        return (
          <button
            key={n}
            onClick={() => onJump(citation)}
            onMouseEnter={(e) => {
              cancelHide()
              setPreview({ citation, rect: e.currentTarget.getBoundingClientRect() })
            }}
            onMouseLeave={scheduleHide}
            title={citation.page != null ? `${citation.doc_title}, page ${citation.page}` : citation.doc_title}
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-px items-center justify-center rounded bg-indigo/20 px-1 align-middle text-[10px] font-semibold tabular-nums text-indigo transition-colors hover:bg-indigo/40 hover:text-cream"
          >
            {n}
          </button>
        )
      })}
    </span>
  )

  // No resolvable citation markers: still render markdown (answers contain
  // markdown regardless of citations), just skip the citation-marker plugin.
  const resolvable = new Set(byNumber.keys())
  const remarkPlugins: PluggableList =
    resolvable.size > 0 ? [remarkGfm, [remarkCitationMarkers, resolvable]] : [remarkGfm]

  return (
    <>
      <div className="text-sm">
        <ReactMarkdown
          remarkPlugins={remarkPlugins}
          components={{
            p: (p) => <p className="whitespace-pre-wrap text-cream" {...p} />,
            li: (p) => <li className="text-cream" {...p} />,
            strong: (p) => <strong className="text-cream" {...p} />,
            code: (p) => <code className="rounded bg-void px-1 text-[13px] text-cream" {...p} />,
            // @ts-expect-error -- custom element emitted by remarkCitationMarkers
            "citation-marker": ({ numbers }: { numbers: string }) =>
              renderChip(
                numbers.split(",").map((n) => parseInt(n, 10)),
                numbers
              ),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
      {preview && (
        <SourceCard
          preview={preview}
          onEnter={cancelHide}
          onLeave={scheduleHide}
          onJump={() => {
            onJump(preview.citation)
            setPreview(null)
          }}
        />
      )}
    </>
  )
}

function SourceCard({
  preview,
  onEnter,
  onLeave,
  onJump,
}: {
  preview: Preview
  onEnter: () => void
  onLeave: () => void
  onJump: () => void
}) {
  const { citation, rect } = preview

  // Clamp horizontally to the viewport; open above the chip when the lower
  // half of the screen is tight, otherwise below.
  const left = Math.max(
    CARD_MARGIN,
    Math.min(rect.left - CARD_WIDTH / 2 + rect.width / 2, window.innerWidth - CARD_WIDTH - CARD_MARGIN)
  )
  const openAbove = rect.bottom > window.innerHeight * 0.6
  const positionStyle: React.CSSProperties = openAbove
    ? { left, bottom: window.innerHeight - rect.top + CARD_MARGIN }
    : { left, top: rect.bottom + CARD_MARGIN }

  return (
    <div
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      style={{ ...positionStyle, width: CARD_WIDTH }}
      className="glass fixed z-50 rounded-xl border border-surface shadow-glow"
    >
      <div className="flex items-center justify-between gap-2 border-b border-surface px-3 py-2">
        <span className="min-w-0 truncate text-xs font-semibold text-cream">
          {citation.doc_title}
        </span>
        <span className="shrink-0 rounded bg-indigo/15 px-1.5 py-0.5 text-[10px] font-medium text-indigo">
          {citation.page != null ? `Page ${citation.page}` : "Source"}
        </span>
      </div>
      <p className="max-h-40 overflow-y-auto whitespace-pre-wrap px-3 py-2 text-[12px] leading-relaxed text-cream-dim">
        {citation.excerpt}
      </p>
      {citation.page != null && (
        <button
          onClick={onJump}
          className="flex w-full items-center gap-1.5 border-t border-surface px-3 py-2 text-[11px] font-medium text-indigo transition-colors hover:text-cream"
        >
          <ExternalLink className="h-3 w-3" />
          Jump to page {citation.page}
        </button>
      )}
    </div>
  )
}
