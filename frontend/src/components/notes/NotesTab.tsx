"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  Download,
  ListTree,
  Palette,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react"
import { Note, Summary, exportNotesMarkdown, getNotes, getSummaries } from "@/services/api"
import { useNotes } from "@/stores/notes"
import { useReader } from "@/stores/reader"
import { colorHex, colorLabel } from "@/lib/noteColors"
import { clipQuote, quoteWordCount } from "@/lib/noteHighlights"
import ColorPicker from "./ColorPicker"
import ColorManager from "./ColorManager"

const markdownComponents = {
  p: (p: React.ComponentPropsWithoutRef<"p">) => (
    <p className="mb-1.5 text-[12.5px] leading-relaxed text-cream-dim last:mb-0" {...p} />
  ),
  strong: (p: React.ComponentPropsWithoutRef<"strong">) => <strong className="text-cream" {...p} />,
  em: (p: React.ComponentPropsWithoutRef<"em">) => <em className="text-cream" {...p} />,
  code: (p: React.ComponentPropsWithoutRef<"code">) => (
    <code className="rounded bg-void px-1 py-0.5 text-[11px] text-cream" {...p} />
  ),
  ul: (p: React.ComponentPropsWithoutRef<"ul">) => (
    <ul className="mb-1.5 list-disc space-y-0.5 pl-4 text-[12.5px] text-cream-dim last:mb-0" {...p} />
  ),
}

interface NotesTabProps {
  docId: number
}

export default function NotesTab({ docId }: NotesTabProps) {
  const notes = useNotes((s) => s.notes)
  const colors = useNotes((s) => s.colors)
  const loading = useNotes((s) => s.loading)
  const focusedNoteId = useNotes((s) => s.focusedNoteId)
  const load = useNotes((s) => s.load)
  const create = useNotes((s) => s.create)
  const currentPage = useReader((s) => s.currentPage)
  const [managing, setManaging] = useState(false)
  const [pageNoteOpen, setPageNoteOpen] = useState(false)
  const [pageDraft, setPageDraft] = useState("")
  const [pageColor, setPageColor] = useState("amber")
  const [pageError, setPageError] = useState<string | null>(null)
  const [pageSaving, setPageSaving] = useState(false)
  const [query, setQuery] = useState("")
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [groupBySection, setGroupBySection] = useState(false)
  const [sections, setSections] = useState<Summary[]>([])
  const [hits, setHits] = useState<Note[] | null>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    void load(docId)
    getSummaries(docId, "section").then(setSections).catch(() => setSections([]))
  }, [docId, load])

  useEffect(() => {
    const q = query.trim()
    if (!q) {
      setHits(null)
      return
    }
    const t = setTimeout(() => {
      getNotes(docId, { q: q || undefined })
        .then(setHits)
        .catch(() => setHits([]))
    }, 280)
    return () => clearTimeout(t)
  }, [query, docId])

  const savePageNote = async () => {
    const body = pageDraft.trim()
    if (!body) {
      setPageError("Write something first")
      return
    }
    setPageSaving(true)
    setPageError(null)
    try {
      await create({
        document_id: docId,
        page_number: currentPage,
        note: body,
        color: pageColor,
      })
      setPageDraft("")
      setPageNoteOpen(false)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setPageError(typeof detail === "string" ? detail : "Could not save")
    } finally {
      setPageSaving(false)
    }
  }

  const download = async () => {
    setExporting(true)
    try {
      const md = await exportNotesMarkdown(docId)
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `notes-${docId}.md`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  const source = hits ?? notes
  const shown = tagFilter
    ? source.filter((n) => (n.tags || []).some((t) => t.toLowerCase() === tagFilter.toLowerCase()))
    : source
  const allTags = useMemo(() => {
    const set = new Set<string>()
    for (const n of notes) for (const t of n.tags || []) set.add(t)
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [notes])

  const groups = useMemo(() => groupNotes(shown, groupBySection ? sections : []), [shown, groupBySection, sections])

  if (loading) {
    return (
      <div className="space-y-3 p-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="animate-pulse rounded-lg border border-surface p-3">
            <div className="mb-2 h-3 w-3/4 rounded bg-surface" />
            <div className="h-3 w-1/2 rounded bg-surface/60" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setPageNoteOpen((v) => !v)}
          className="flex items-center gap-1 rounded-md bg-indigo/15 px-2 py-1 text-[11px] font-medium text-indigo hover:bg-indigo/25"
        >
          <Plus className="h-3 w-3" />
          New page note
        </button>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setGroupBySection((v) => !v)}
            title="Group by section"
            className={`rounded p-1 ${groupBySection ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:text-cream"}`}
          >
            <ListTree className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => void download()}
            disabled={exporting}
            title="Export notes as markdown"
            className="rounded p-1 text-cream-dim hover:text-cream disabled:opacity-40"
          >
            <Download className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setManaging((v) => !v)}
            title="Manage highlight colours"
            className={`rounded p-1 ${managing ? "bg-indigo/15 text-indigo" : "text-cream-dim hover:text-cream"}`}
          >
            <Palette className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-cream-dim" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search notes in this document…"
          className="w-full rounded-md border border-surface bg-void py-1 pl-6 pr-2 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
        />
      </div>

      {allTags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {allTags.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTagFilter((cur) => (cur === t ? null : t))}
              className={`rounded-full px-2 py-0.5 text-[10px] ${
                tagFilter === t ? "bg-indigo/25 text-indigo" : "bg-surface text-cream-dim hover:text-cream"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {managing && <ColorManager onClose={() => setManaging(false)} />}

      {pageNoteOpen && (
        <div className="rounded-lg border border-surface bg-void/60 p-3">
          <p className="mb-1.5 text-[11px] text-cream-dim">Note on page {currentPage} (no text selected)</p>
          <textarea
            autoFocus
            value={pageDraft}
            onChange={(e) => setPageDraft(e.target.value)}
            rows={3}
            placeholder="Your words…"
            className="w-full resize-none rounded-md border border-surface bg-panel px-2 py-1.5 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
          />
          <div className="mt-2 flex items-center justify-between">
            <ColorPicker colors={colors} value={pageColor} onChange={setPageColor} size="sm" />
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setPageNoteOpen(false)}
                className="rounded px-2 py-1 text-[11px] text-cream-dim hover:text-cream"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={pageSaving}
                onClick={() => void savePageNote()}
                className="rounded bg-indigo/20 px-2 py-1 text-[11px] font-medium text-indigo hover:bg-indigo/30 disabled:opacity-50"
              >
                {pageSaving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          {pageError && <p className="mt-1.5 text-[11px] text-rust">{pageError}</p>}
        </div>
      )}

      {shown.length === 0 ? (
        <p className="py-8 text-center text-sm text-cream-dim">
          {query || tagFilter
            ? "No notes match."
            : "Select text on the page to highlight or add a note. Double-click a word, or press H / N."}
        </p>
      ) : (
        groups.map((g) => (
          <div key={g.title}>
            {groups.length > 1 && (
              <p className="mb-1.5 mt-2 text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
                {g.title}
              </p>
            )}
            <div className="space-y-2">
              {g.notes.map((n) => (
                <NoteCard
                  key={n.id}
                  note={n}
                  hex={colorHex(colors, n.color)}
                  colorName={colorLabel(
                    colors.find((c) => c.id === n.color) || {
                      id: n.color,
                      name: n.color,
                      hex: colorHex(colors, n.color),
                      built_in: false,
                    }
                  )}
                  focused={focusedNoteId === n.id}
                  showDoc={false}
                />
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  )
}

function groupNotes(notes: Note[], sections: Summary[]): { title: string; notes: Note[] }[] {
  if (sections.length === 0) return [{ title: "Notes", notes }]
  const buckets = new Map<string, Note[]>()
  const other: Note[] = []
  for (const n of notes) {
    const sec = sections.find((s) => {
      const start = s.start_page ?? s.page_number
      const end = s.end_page ?? s.start_page ?? s.page_number
      if (start == null) return false
      return n.page_number >= start && n.page_number <= (end ?? start)
    })
    if (!sec) {
      other.push(n)
      continue
    }
    const title = sec.section_title || "Untitled section"
    const list = buckets.get(title) || []
    list.push(n)
    buckets.set(title, list)
  }
  const groups = sections
    .map((s) => s.section_title || "Untitled section")
    .filter((title, i, arr) => arr.indexOf(title) === i)
    .filter((title) => buckets.has(title))
    .map((title) => ({ title, notes: buckets.get(title)! }))
  if (other.length) groups.push({ title: "Other", notes: other })
  return groups.length ? groups : [{ title: "Notes", notes }]
}

function NoteCard({
  note,
  hex,
  colorName,
  focused,
  showDoc,
}: {
  note: Note
  hex: string
  colorName: string
  focused: boolean
  showDoc: boolean
}) {
  const update = useNotes((s) => s.update)
  const remove = useNotes((s) => s.remove)
  const colors = useNotes((s) => s.colors)
  const focusNote = useNotes((s) => s.focusNote)
  const makeFlashcard = useNotes((s) => s.makeFlashcard)
  const jumpToCitation = useReader((s) => s.jumpToCitation)
  const setPage = useReader((s) => s.setPage)
  const selectedDocId = useReader((s) => s.selectedDocId)
  const selectDoc = useReader((s) => s.selectDoc)
  const router = useRouter()
  const cardRef = useRef<HTMLDivElement>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note.note || "")
  const [color, setColor] = useState(note.color)
  const [tagDraft, setTagDraft] = useState((note.tags || []).join(", "))
  const [saving, setSaving] = useState(false)
  const [cardBusy, setCardBusy] = useState(false)
  const [cardError, setCardError] = useState<string | null>(null)

  useEffect(() => {
    if (focused && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focused])

  const jump = () => {
    focusNote(note.id)
    // In "All docs" search a note can belong to a different document than the
    // one open in the reader; open its document first so the jump lands there.
    if (note.document_id !== selectedDocId) {
      selectDoc(note.document_id, undefined, note.page_number)
      router.push(`/dashboard/read/${note.document_id}`)
    }
    if (note.quote) {
      jumpToCitation({
        doc_id: note.document_id,
        page: note.page_number,
        evidence: { exact: note.quote },
      })
    }
    else setPage(note.page_number)
  }

  const save = async () => {
    setSaving(true)
    try {
      const tags = tagDraft
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
      await update(note.id, { note: draft, color, tags })
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const makeCard = async () => {
    setCardBusy(true)
    setCardError(null)
    try {
      await makeFlashcard(note.id)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setCardError(typeof detail === "string" ? detail : "Could not make a flashcard")
    } finally {
      setCardBusy(false)
    }
  }

  return (
    <div
      ref={cardRef}
      className={`rounded-xl border border-surface/80 bg-void/40 px-3.5 py-2.5 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] ${
        focused ? "ring-1 ring-indigo/50" : ""
      }`}
      style={{ borderLeftWidth: 3, borderLeftColor: hex }}
    >
      {showDoc && note.document_title && (
        <p className="mb-1 truncate text-[10px] text-cream-dim">{note.document_title}</p>
      )}
      {note.quote && (
        <div className="mb-1.5">
          <p
            className="line-clamp-2 text-[12.5px] italic leading-snug text-cream/85"
            title={note.quote}
          >
            “{clipQuote(note.quote, 110)}”
          </p>
          {quoteWordCount(note.quote) > 22 && (
            <p className="mt-0.5 text-[10px] tabular-nums tracking-wide text-cream-dim/80">
              {quoteWordCount(note.quote)} words
            </p>
          )}
        </div>
      )}
      {editing ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            className="mb-2 w-full resize-none rounded-md border border-surface bg-panel px-2 py-1.5 text-xs text-cream focus:outline-none focus:ring-1 focus:ring-indigo"
          />
          <input
            value={tagDraft}
            onChange={(e) => setTagDraft(e.target.value)}
            placeholder="tags, comma-separated"
            className="mb-2 w-full rounded-md border border-surface bg-panel px-2 py-1 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
          />
          <ColorPicker colors={colors} value={color} onChange={setColor} size="sm" />
          <div className="mt-2 flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => {
                setEditing(false)
                setDraft(note.note || "")
                setColor(note.color)
                setTagDraft((note.tags || []).join(", "))
              }}
              className="text-[11px] text-cream-dim hover:text-cream"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => void save()}
              className="text-[11px] font-medium text-indigo hover:text-cream disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      ) : note.note ? (
        <div className="note-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {note.note}
          </ReactMarkdown>
        </div>
      ) : (
        <p className="text-[12.5px] italic text-cream-dim/70">Highlighted (no note yet)</p>
      )}
      {(note.tags || []).length > 0 && !editing && (
        <div className="mt-1 flex flex-wrap gap-1">
          {(note.tags || []).map((t) => (
            <span key={t} className="rounded-full bg-surface px-1.5 py-0.5 text-[10px] text-cream-dim">
              {t}
            </span>
          ))}
        </div>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-2.5 text-[10.5px] text-cream-dim">
        <button
          type="button"
          onClick={jump}
          className="font-semibold text-indigo hover:text-cream"
          title={`Jump to page ${note.page_number}`}
        >
          p.{note.page_number}
          {note.end_page && note.end_page !== note.page_number ? `–${note.end_page}` : ""}{" "}
          ↗
        </button>
        <span title={colorName} className="capitalize">
          {colorName}
        </span>
        <button
          type="button"
          onClick={() => {
            setDraft(note.note || "")
            setColor(note.color)
            setTagDraft((note.tags || []).join(", "))
            setEditing(true)
          }}
          className="inline-flex items-center gap-0.5 hover:text-cream"
        >
          <Pencil className="h-3 w-3" />
          {note.note ? "Edit" : "+ Note"}
        </button>
        {note.flashcard_id ? (
          <span className="text-indigo">Card made</span>
        ) : (
          <button
            type="button"
            disabled={cardBusy}
            onClick={() => void makeCard()}
            className="inline-flex items-center gap-0.5 hover:text-cream disabled:opacity-50"
            title="Turn this note into a flashcard"
          >
            <Sparkles className="h-3 w-3" />
            {cardBusy ? "Making…" : "Flashcard"}
          </button>
        )}
        <button
          type="button"
          onClick={() => void remove(note.id)}
          className="inline-flex items-center gap-0.5 hover:text-rust"
          title="Delete"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      {cardError && <p className="mt-1 text-[11px] text-rust">{cardError}</p>}
    </div>
  )
}
