"use client"

import { ExternalLink, X } from "lucide-react"

interface PopupCitation {
  doc_id: number
  doc_title: string
  /** Page number, or null for non-paged sources (e.g. knowledge graph). */
  page: number | null
  excerpt: string
}

interface CitationPopupProps {
  citations: PopupCitation[]
  onClose: () => void
  onJump?: (citation: PopupCitation) => void
}

export default function CitationPopup({ citations, onClose, onJump }: CitationPopupProps) {
  if (!citations || citations.length === 0) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass m-4 flex max-h-[80vh] w-full max-w-2xl flex-col rounded-2xl border border-surface shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-surface px-5 py-3">
          <h3 className="font-display text-base font-bold text-cream">Sources</h3>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="reader-scroll flex-1 space-y-3 overflow-y-auto p-5">
          {citations.map((citation, idx) => (
            <div
              key={idx}
              className="rounded-lg border border-surface bg-void/50 p-4 transition-colors hover:border-indigo/40"
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <h4 className="min-w-0 flex-1 truncate text-sm font-semibold text-cream">
                  {citation.doc_title || "Untitled document"}
                </h4>
                <span className="shrink-0 rounded bg-indigo/15 px-2 py-0.5 text-xs font-medium text-indigo">
                  {citation.page != null ? `Page ${citation.page}` : "Source"}
                </span>
              </div>
              <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-cream-dim line-clamp-3">
                {citation.excerpt}
              </p>
              {onJump && citation.page != null && (
                <button
                  onClick={() => {
                    onJump(citation)
                    onClose()
                  }}
                  className="mt-3 flex items-center gap-1.5 text-xs font-medium text-indigo transition-colors hover:text-cream"
                >
                  <ExternalLink className="h-3 w-3" />
                  Jump to source
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
