"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, BookOpen, Check, ChevronLeft, ChevronRight, FolderOpen, Headphones, Loader2, Search, Sparkles, UsersRound, X } from "lucide-react"
import TopBar from "@/components/layout/TopBar"
import { useAuth } from "@/stores/auth"
import { getNotebooks, getUnfiledNotebook } from "@/services/notebookApi"
import { getSharedLibrary } from "@/services/sharedLibraryApi"
import type { SharedDocument } from "@/services/types"
import SharedAddDialog from "./SharedAddDialog"
import SharedPreviewDialog from "./SharedPreviewDialog"

const PAGE_SIZE = 24

export default function SharedLibraryPage() {
  return <Suspense fallback={<div className="library-shell shared-library-shell" />}><SharedLibraryContent /></Suspense>
}

function SharedLibraryContent() {
  const searchParams = useSearchParams()
  const notebookParam = searchParams.get("notebook")
  const notebookId = notebookParam ? Number(notebookParam) : null
  const user = useAuth((state) => state.user)
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [topic, setTopic] = useState("")
  const [documentType, setDocumentType] = useState("")
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Record<number, SharedDocument>>({})
  const [addOpen, setAddOpen] = useState(false)
  const [previewDocument, setPreviewDocument] = useState<SharedDocument | null>(null)
  const [notice, setNotice] = useState("")
  const notebooksQuery = useQuery({
    queryKey: ["notebooks", user?.id],
    queryFn: ({ signal }) => getNotebooks(signal),
    enabled: user != null,
    retry: 1,
  })
  const unfiledQuery = useQuery({
    queryKey: ["unfiled-notebook", user?.id],
    queryFn: ({ signal }) => getUnfiledNotebook(signal),
    enabled: user != null,
    staleTime: 60_000,
  })
  const unfiledName = unfiledQuery.data?.name || "Unfiled"
  const notebooks = notebooksQuery.data ?? []
  const destinationNotebook = notebookId == null ? null : notebooks.find((notebook) => notebook.id === notebookId) ?? null
  const invalidNotebook = notebookParam != null && (!Number.isSafeInteger(notebookId) || Number(notebookId) < 1)
  const missingNotebook = notebookParam != null && !notebooksQuery.isPending && !notebooksQuery.isError && !destinationNotebook

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    setPage(1)
    setSelected({})
  }, [debouncedSearch, topic, documentType, notebookId])

  const query = useQuery({
    queryKey: ["shared-library", user?.id, { search: debouncedSearch, topic, documentType, notebookId, page }],
    queryFn: ({ signal }) => getSharedLibrary({
      search: debouncedSearch,
      topic,
      documentType,
      notebookId: notebookId ?? undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }, signal),
    enabled: user != null && !invalidNotebook && !missingNotebook,
    retry: 1,
  })
  const documents = query.data?.documents ?? []
  const pageCount = Math.max(1, Math.ceil((query.data?.total ?? 0) / PAGE_SIZE))
  const selectedDocuments = useMemo(() => Object.values(selected), [selected])

  useEffect(() => {
    if (query.data && page > pageCount) setPage(pageCount)
  }, [query.data, page, pageCount])

  const toggleSelected = (document: SharedDocument) => {
    if (document.added) return
    setSelected((current) => {
      if (current[document.id]) {
        const next = { ...current }
        delete next[document.id]
        return next
      }
      return { ...current, [document.id]: document }
    })
    setNotice("")
  }

  const clearFilters = () => {
    setSearch("")
    setDebouncedSearch("")
    setTopic("")
    setDocumentType("")
    setPage(1)
    setSelected({})
  }

  const invalidateMembership = () => {
    void queryClient.invalidateQueries({ queryKey: ["shared-library"] })
    void queryClient.invalidateQueries({ queryKey: ["documents"] })
    void queryClient.invalidateQueries({ queryKey: ["library"] })
    void queryClient.invalidateQueries({ queryKey: ["notebooks"] })
  }

  if (invalidNotebook || missingNotebook) {
    return <div className="library-shell shared-library-shell"><TopBar library /><main className="library-main"><div className="library-empty" role="alert"><AlertCircle size={32} /><h1>This notebook is unavailable</h1><p>Return to your notebooks to choose another destination.</p><Link href="/dashboard" className="library-add-button">Your notebooks</Link></div></main></div>
  }

  return (
    <div className="library-shell shared-library-shell">
      <TopBar library />
      <div className="library-layout">
        <aside className="library-navigation" aria-label="Library navigation">
          <p className="library-nav-label">Your library</p>
          <nav>
            <Link href="/dashboard" className="library-nav-item"><FolderOpen size={17} /><span>Notebooks</span></Link>
            <Link href="/dashboard/shared" aria-current="page" className="library-nav-item is-active"><UsersRound size={17} /><span>Shared library</span></Link>
            <Link href="/dashboard?view=podcasts" className="library-nav-item"><Headphones size={17} /><span>Podcasts</span></Link>
          </nav>
          <div className="library-storage-note shared-library-note"><BookOpen size={19} strokeWidth={1.5} /><p>Good papers travel.<br />Your notes stay yours.</p></div>
        </aside>
        <main className="library-main shared-library-main" id="shared-library-main">
          <div className="shared-library-intro">
            <div>
              <p className="shared-library-kicker"><UsersRound size={15} />Team collection</p>
              <h1>Shared library</h1>
              <p className="shared-library-description">Find papers your team has published, preview them here, and add the ones worth keeping to your own notebooks.</p>
            </div>
            {destinationNotebook && <div className="shared-destination-note"><span>Suggestions for</span><strong>{destinationNotebook.name}</strong><Link href="/dashboard/shared">Browse the full collection</Link></div>}
          </div>

          {notebooksQuery.isError && <div className="notebook-load-error" role="alert">Your notebooks could not be loaded. You can still add papers to {unfiledName}.<button type="button" onClick={() => void notebooksQuery.refetch()}>Try again</button></div>}

          <section className="shared-catalog" aria-labelledby="shared-catalog-heading">
            <div className="library-section-heading shared-catalog-heading">
              <div><h2 id="shared-catalog-heading">Browse papers</h2><span className="library-result-count" aria-live="polite">{query.data ? `${query.data.total} ${query.data.total === 1 ? "paper" : "papers"}` : "Loading collection"}</span></div>
              {selectedDocuments.length > 0 && <div className="shared-selection-actions"><span>{selectedDocuments.length} selected</span><button type="button" className="shared-clear-selection" onClick={() => setSelected({})}>Clear</button><button type="button" className="library-add-button" onClick={() => setAddOpen(true)}>Add to my notebook</button></div>}
            </div>
            {notice && <p className="shared-library-notice" role="status"><Check size={15} />{notice}</p>}
            <div className="shared-toolbar">
              <label className="library-search shared-search"><Search size={17} aria-hidden="true" /><input aria-label="Search shared papers" type="search" placeholder="Search title, author, or topic" value={search} onChange={(event) => { setSearch(event.target.value); setSelected({}) }} />{query.isFetching && <Loader2 size={15} className="animate-spin" aria-label="Searching shared papers" />}</label>
              <select aria-label="Topic" value={topic} onChange={(event) => { setTopic(event.target.value); setSelected({}) }}><option value="">All topics</option>{(query.data?.topics ?? []).map((item) => <option key={item} value={item}>{item}</option>)}</select>
              <select aria-label="Document type" value={documentType} onChange={(event) => { setDocumentType(event.target.value); setSelected({}) }}><option value="">All document types</option>{(query.data?.document_types ?? []).map((item) => <option key={item} value={item}>{item}</option>)}</select>
              {(debouncedSearch || topic || documentType) && <button type="button" className="shared-clear-filters" onClick={clearFilters}><X size={14} />Clear filters</button>}
            </div>

            {query.isPending ? (
              <div className="shared-document-grid" aria-label="Loading shared papers">{Array.from({ length: 8 }, (_, index) => <div key={index} className="library-card-skeleton"><div /><span /><span /></div>)}</div>
            ) : query.isError ? (
              <div className="library-empty" role="alert"><AlertCircle size={32} /><h2>The shared library could not be loaded</h2><p>Check your connection and try again.</p><button type="button" className="library-add-button" onClick={() => void query.refetch()}>Try again</button></div>
            ) : documents.length === 0 ? (
              <div className="library-empty shared-empty"><BookOpen size={40} strokeWidth={1.3} /><h2>{debouncedSearch || topic || documentType ? "No papers match these filters" : "Nothing has been shared yet"}</h2><p>{debouncedSearch || topic || documentType ? "Clear a filter or search for a broader topic." : "When someone publishes a paper to the team, it will appear here."}</p>{(debouncedSearch || topic || documentType) && <button type="button" className="library-secondary-button" onClick={clearFilters}>Clear filters</button>}</div>
            ) : (
              <div className="shared-document-grid" aria-busy={query.isFetching}>
                {documents.map((document) => <SharedDocumentCard key={document.id} document={document} selected={Boolean(selected[document.id])} onSelect={() => toggleSelected(document)} onPreview={() => setPreviewDocument(document)} />)}
              </div>
            )}

            {query.data && query.data.total > PAGE_SIZE && <nav className="library-pagination" aria-label="Shared library pages"><span>Page {page} of {pageCount}</span><div><button type="button" aria-label="Previous page" disabled={page === 1 || query.isPlaceholderData} onClick={() => setPage((current) => current - 1)}><ChevronLeft size={17} />Previous</button><button type="button" aria-label="Next page" disabled={page >= pageCount || query.isPlaceholderData} onClick={() => setPage((current) => current + 1)}>Next<ChevronRight size={17} /></button></div></nav>}
          </section>
        </main>
      </div>
      {addOpen && <SharedAddDialog documents={selectedDocuments} notebooks={notebooks} defaultNotebookId={destinationNotebook?.id ?? null} unfiledName={unfiledName} onClose={() => setAddOpen(false)} onAdded={(result) => {
        setAddOpen(false)
        setSelected({})
        setNotice(`${result.added} ${result.added === 1 ? "paper added" : "papers added"}${result.alreadyAdded ? `, ${result.alreadyAdded} already in your library` : ""}.`)
        invalidateMembership()
      }} />}
      {previewDocument && <SharedPreviewDialog document={previewDocument} onClose={() => setPreviewDocument(null)} />}
    </div>
  )
}

function SharedDocumentCard({ document, selected, onSelect, onPreview }: {
  document: SharedDocument
  selected: boolean
  onSelect: () => void
  onPreview: () => void
}) {
  return (
    <article className={`shared-document-card ${selected ? "is-selected" : ""} ${document.added ? "is-added" : ""}`}>
      <button type="button" className="shared-document-select" onClick={onSelect} aria-pressed={selected} disabled={document.added} aria-label={document.added ? `${document.title}, already in your library` : `${selected ? "Deselect" : "Select"} ${document.title}`}>
        <span className="shared-document-cover" aria-hidden="true"><span className="shared-cover-rule" /><BookOpen size={20} strokeWidth={1.4} /><span className="shared-cover-title">{document.title}</span><span className="shared-cover-type">PDF · {document.document_type}</span></span>
        <span className="shared-document-info">
          <span className="shared-document-meta"><span>{document.document_type}</span><span>{document.num_pages ? `${document.num_pages} pages` : "PDF"}</span></span>
          <span className="shared-document-title">{document.title}</span>
          {document.author && <span className="shared-document-author">{document.author}</span>}
          {document.description && <span className="shared-document-description">{document.description}</span>}
          <span className="shared-topic-list">{document.topics.slice(0, 3).map((item) => <span key={item}>{item}</span>)}</span>
          {document.reason && <span className="shared-document-reason"><Sparkles size={13} />{document.reason}</span>}
          <span className={`shared-added-label ${document.added ? "is-visible" : ""}`}>{document.added ? <><Check size={13} />In your library</> : selected ? <><Check size={13} />Selected</> : "Select paper"}</span>
        </span>
      </button>
      <div className="shared-document-actions"><span>Shared {formatSharedDate(document.shared_at)}</span><button type="button" onClick={onPreview} aria-label={`Preview ${document.title}`}>Preview</button></div>
    </article>
  )
}

function formatSharedDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "recently"
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date)
}
