"use client"

import { useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Summary, getSummaries, generateSectionSummary } from "@/services/api"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import { Headphones, ChevronDown, ChevronRight, StickyNote, AlignLeft, Loader2, Sparkles } from "lucide-react"
import NotesTab from "@/components/notes/NotesTab"
import { useNotes } from "@/stores/notes"

const markdownComponents = {
  h1: (p: React.ComponentPropsWithoutRef<"h1">) => (
    <h1 className="mb-1.5 mt-3 text-sm font-semibold text-cream first:mt-0" {...p} />
  ),
  h2: (p: React.ComponentPropsWithoutRef<"h2">) => (
    <h2 className="mb-1.5 mt-3 text-sm font-semibold text-cream first:mt-0" {...p} />
  ),
  h3: (p: React.ComponentPropsWithoutRef<"h3">) => (
    <h3 className="mb-1 mt-2.5 text-sm font-semibold text-cream first:mt-0" {...p} />
  ),
  p: (p: React.ComponentPropsWithoutRef<"p">) => (
    <p className="mb-2 text-[13px] leading-relaxed text-cream-dim last:mb-0" {...p} />
  ),
  ul: (p: React.ComponentPropsWithoutRef<"ul">) => (
    <ul className="mb-2 list-disc space-y-1 pl-4 text-[13px] leading-relaxed text-cream-dim last:mb-0" {...p} />
  ),
  ol: (p: React.ComponentPropsWithoutRef<"ol">) => (
    <ol className="mb-2 list-decimal space-y-1 pl-4 text-[13px] leading-relaxed text-cream-dim last:mb-0" {...p} />
  ),
  li: (p: React.ComponentPropsWithoutRef<"li">) => <li className="text-cream-dim" {...p} />,
  strong: (p: React.ComponentPropsWithoutRef<"strong">) => <strong className="text-cream" {...p} />,
  em: (p: React.ComponentPropsWithoutRef<"em">) => <em className="text-cream" {...p} />,
  code: (p: React.ComponentPropsWithoutRef<"code">) => (
    <code className="rounded bg-void px-1 py-0.5 text-[12px] text-cream" {...p} />
  ),
  a: (p: React.ComponentPropsWithoutRef<"a">) => (
    <a className="text-indigo underline hover:text-cream" target="_blank" rel="noreferrer" {...p} />
  ),
  blockquote: (p: React.ComponentPropsWithoutRef<"blockquote">) => (
    <blockquote className="mb-2 border-l-2 border-surface pl-3 text-cream-dim last:mb-0" {...p} />
  ),
}

interface SummaryPanelProps {
  docId: number
}

const SECTION_POLL_INTERVAL_MS = 5000
const SECTION_POLL_MAX_ATTEMPTS = 90 // ~7.5 minutes

export default function SummaryPanel({ docId }: SummaryPanelProps) {
  const [pageSummaries, setPageSummaries] = useState<Summary[]>([])
  const [sectionSummaries, setSectionSummaries] = useState<Summary[]>([])
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<"notes" | "section">("notes")
  const [generatingTitles, setGeneratingTitles] = useState<Set<string>>(new Set())
  const { setPage, currentPage } = useReader()
  const pollTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const noteCount = useNotes((s) => (s.docId === docId ? s.notes.length : 0))
  const focusedNoteId = useNotes((s) => s.focusedNoteId)

  useEffect(() => {
    if (focusedNoteId != null) setActiveTab("notes")
  }, [focusedNoteId])

  useEffect(() => {
    let mounted = true
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    // Sections are detected up front (fast, no LLM calls) so they don't need
    // polling here - only page summaries still run as a slow whole-book
    // background job that can take anywhere from seconds to hours, so keep
    // polling for those until they show up.
    let pollCount = 0
    let lastCount = -1
    let stableRounds = 0
    const MAX_POLLS = 360 // ~1 hour at 10s intervals
    const STABLE_ROUNDS_TO_STOP = 3 // stop once the count hasn't grown for 3 polls

    const fetchData = async (isPoll: boolean) => {
      try {
        const [pages, sections] = await Promise.all([
          getSummaries(docId, "page"),
          getSummaries(docId, "section"),
        ])
        if (!mounted) return
        setPageSummaries(pages)
        setSectionSummaries(sections)

        pollCount += 1
        stableRounds = pages.length > lastCount ? 0 : stableRounds + 1
        lastCount = pages.length
        // Keep polling while the page count is still growing (hours-long
        // whole-book backfill); stop once it's stabilized for a few polls.
        if (stableRounds < STABLE_ROUNDS_TO_STOP && pollCount < MAX_POLLS) {
          pollTimer = setTimeout(() => fetchData(true), 10000)
        }
      } catch {
        console.error("Failed to load summaries")
      } finally {
        if (mounted && !isPoll) setLoading(false)
      }
    }
    setLoading(true)
    fetchData(false)
    // Capture the Map now rather than reading pollTimers.current at cleanup
    // time. The ref holds one Map for the component's whole life, so the two
    // are the same object today - this just stops the rule from having to
    // take that on trust, and keeps it true if the ref is ever reassigned.
    const timers = pollTimers.current
    return () => {
      mounted = false
      if (pollTimer) clearTimeout(pollTimer)
      timers.forEach((t) => clearTimeout(t))
      timers.clear()
    }
  }, [docId])

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const stopGenerating = (sectionTitle: string) => {
    const timer = pollTimers.current.get(sectionTitle)
    if (timer) {
      clearTimeout(timer)
      pollTimers.current.delete(sectionTitle)
    }
    setGeneratingTitles((prev) => {
      const next = new Set(prev)
      next.delete(sectionTitle)
      return next
    })
  }

  const pollSection = (sectionTitle: string, attempt = 0) => {
    generateSectionSummary(docId, sectionTitle)
      .then((updated) => {
        setSectionSummaries((prev) =>
          prev.map((s) => (s.section_title === sectionTitle ? updated : s))
        )
        if (updated.content || updated.status === "failed") {
          stopGenerating(sectionTitle)
          return
        }
        if (attempt >= SECTION_POLL_MAX_ATTEMPTS) {
          stopGenerating(sectionTitle)
          return
        }
        const timer = setTimeout(() => pollSection(sectionTitle, attempt + 1), SECTION_POLL_INTERVAL_MS)
        pollTimers.current.set(sectionTitle, timer)
      })
      .catch(() => stopGenerating(sectionTitle))
  }

  const handleGenerate = (sectionTitle: string) => {
    if (!sectionTitle || generatingTitles.has(sectionTitle)) return
    setGeneratingTitles((prev) => new Set(prev).add(sectionTitle))
    pollSection(sectionTitle)
  }

  const summaries = sectionSummaries
  const generatedSummaries = summaries.filter((s) => !!s.content)
  const allExpanded = generatedSummaries.length > 0 && generatedSummaries.every((s) => expanded.has(s.id))

  if (loading) {
    return (
      <div className="space-y-4 p-6">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="animate-pulse">
            <div className="mb-2 h-4 w-1/3 rounded bg-surface" />
            <div className="h-3 w-3/4 rounded bg-surface/60" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="reader-scroll flex h-full flex-col overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-surface bg-panel/95 p-3 backdrop-blur">
        <div className="flex gap-1 rounded-lg bg-void p-1">
          <button
            onClick={() => setActiveTab("notes")}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "notes" ? "gradient-primary text-white" : "text-cream-dim hover:text-cream"
            }`}
          >
            <StickyNote className="h-3.5 w-3.5" />
            Notes ({noteCount})
          </button>
          <button
            onClick={() => setActiveTab("section")}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "section" ? "gradient-primary text-white" : "text-cream-dim hover:text-cream"
            }`}
          >
            <AlignLeft className="h-3.5 w-3.5" />
            Sections ({sectionSummaries.length})
          </button>
        </div>
        {activeTab === "section" && (
          <div className="mt-2 flex justify-end">
            <button
              onClick={() =>
                setExpanded(allExpanded ? new Set() : new Set(generatedSummaries.map((s) => s.id)))
              }
              disabled={generatedSummaries.length === 0}
              className="text-[11px] text-cream-dim transition-colors hover:text-cream disabled:opacity-40"
            >
              {allExpanded ? "Collapse all" : "Expand all"}
            </button>
          </div>
        )}
      </div>

      {activeTab === "notes" ? (
        <NotesTab docId={docId} />
      ) : (
      <div className="space-y-2 p-3">
        {summaries.length === 0 ? (
          <p className="py-8 text-center text-sm text-cream-dim">
            No sections detected yet.
          </p>
        ) : (
          summaries.map((s) => {
            const isSection = true
            const isOpen = expanded.has(s.id)
            const isCurrent = false
            const hasContent = !!s.content
            const isGenerating =
              isSection && (s.status === "generating" || generatingTitles.has(s.section_title || ""))
            const failed = isSection && s.status === "failed" && !hasContent
            const jumpTarget = isSection ? s.start_page : s.page_number

            return (
              <div
                key={s.id}
                className={`overflow-hidden rounded-lg border transition-colors ${
                  isCurrent ? "border-indigo/40 shadow-glow" : "border-surface"
                } bg-void/50`}
              >
                <div className="group flex w-full items-center gap-2 border-l-2 border-transparent p-3 text-left transition-colors hover:border-indigo hover:bg-surface/50">
                  <button
                    onClick={() => hasContent && toggle(s.id)}
                    disabled={!hasContent}
                    className="shrink-0 disabled:opacity-30"
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3.5 w-3.5 text-cream-dim" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 text-cream-dim" />
                    )}
                  </button>
                  <button
                    onClick={() => jumpTarget && setPage(jumpTarget)}
                    title={jumpTarget ? `Jump to page ${jumpTarget}` : undefined}
                    className="min-w-0 flex-1 truncate text-left text-sm font-semibold text-cream transition-colors hover:text-indigo"
                  >
                    {isSection ? s.section_title || "Untitled section" : `Page ${s.page_number}`}
                    {isSection && s.start_page != null && (
                      <span className="ml-1.5 text-[10px] font-normal text-cream-dim">
                        p.{s.start_page}
                        {s.end_page != null && s.end_page !== s.start_page ? `-${s.end_page}` : ""}
                      </span>
                    )}
                  </button>

                  {isSection && !hasContent && !isGenerating && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleGenerate(s.section_title || "")
                      }}
                      className="flex shrink-0 items-center gap-1 rounded-md bg-indigo/15 px-2 py-1 text-[11px] font-medium text-indigo transition-colors hover:bg-indigo/25"
                    >
                      <Sparkles className="h-3 w-3" />
                      {failed ? "Retry" : "Generate"}
                    </button>
                  )}
                  {isSection && isGenerating && (
                    <span className="flex shrink-0 items-center gap-1 text-[11px] text-cream-dim">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Generating
                    </span>
                  )}

                  {hasContent && (
                    <>
                      <span className="shrink-0 text-[10px] text-cream-dim">
                        {s.content.split(" ").length}w
                      </span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          void ttsEngine.playSummaryAudio(s.id)
                        }}
                        title="Listen to this summary"
                        className="shrink-0 rounded p-1 text-cream-dim opacity-0 transition-all hover:bg-indigo/15 hover:text-indigo group-hover:opacity-100"
                      >
                        <Headphones className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>
                {isOpen && hasContent && (
                  <div className="border-t border-surface px-4 py-3">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                      {s.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            )
          })
        )}
        {pageSummaries.length > 0 && (
          <details className="mt-4 rounded-lg border border-surface bg-void/40">
            <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium text-cream-dim hover:text-cream">
              Page summaries ({pageSummaries.length})
            </summary>
            <div className="space-y-2 border-t border-surface p-2">
              {pageSummaries.map((s) => (
                <div key={s.id} className="rounded-md border border-surface bg-void/50 px-3 py-2">
                  <button
                    type="button"
                    onClick={() => s.page_number && setPage(s.page_number)}
                    className={`text-left text-sm font-semibold ${
                      s.page_number === currentPage ? "text-indigo" : "text-cream hover:text-indigo"
                    }`}
                  >
                    Page {s.page_number}
                  </button>
                  {s.content && (
                    <div className="mt-1">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                        {s.content}
                      </ReactMarkdown>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
      )}
    </div>
  )
}
