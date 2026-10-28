"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { BookOpen, ChevronLeft, ChevronRight, Clock3, FileText, FolderOpen, Headphones, Library, Loader2, Plus, Search, AlertCircle, LayoutGrid, List, X, UsersRound } from "lucide-react"
import PodcastGallery from "./PodcastGallery"
import TopBar from "@/components/layout/TopBar"
import UploadModal from "@/components/layout/UploadModal"
import DocumentCard from "./DocumentCard"
import { getLibrary, LIBRARY_PAGE_SIZE, type LibraryShelf, type LibrarySort } from "@/services/libraryApi"
import { useAuth } from "@/stores/auth"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import { getNotebooks, moveDocuments, type Notebook } from "@/services/notebookApi"
import NotebookShelf from "./NotebookShelf"
import NotebookDialog, { notebookError } from "./NotebookDialog"
import MoveDocumentsDialog from "./MoveDocumentsDialog"

const SHELVES: { id: LibraryShelf; label: string; icon: typeof Library }[] = [
  { id: "all", label: "All documents", icon: Library },
  { id: "reading", label: "In progress", icon: BookOpen },
  { id: "unread", label: "Not started", icon: Clock3 },
  { id: "processing", label: "Processing", icon: Loader2 },
  { id: "failed", label: "Needs attention", icon: AlertCircle },
]

export default function LibraryPage() {
  return <Suspense fallback={<div className="library-shell" />}><LibraryContent /></Suspense>
}

function LibraryContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const notebookParam = searchParams.get("notebook")
  const notebookId = notebookParam ? Number(notebookParam) : null
  const unfiled = searchParams.get("unfiled") === "true"
  const moveToParam = searchParams.get("move_to")
  const moveToId = moveToParam ? Number(moveToParam) : null
  const viewParam = searchParams.get("view")
  const isPodcastsView = viewParam === "podcasts"
  const home = !notebookParam && !unfiled && viewParam !== "documents" && viewParam !== "podcasts"
  const [sourceNotebook, setSourceNotebook] = useState("")
  const picking = Boolean(moveToId)
  const filterNotebookId = picking ? Number(sourceNotebook) || null : notebookId
  const filterUnfiled = picking ? sourceNotebook === "unfiled" : unfiled
  const scope = filterUnfiled ? "unfiled" : filterNotebookId ?? "all"
  const [editingNotebook, setEditingNotebook] = useState<Notebook | null | undefined>(undefined)
  const [selected, setSelected] = useState<number[]>([])
  const [moveOpen, setMoveOpen] = useState(false)
  const [moving, setMoving] = useState(false)
  const [actionError, setActionError] = useState("")
  const user = useAuth((state) => state.user)
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [shelf, setShelf] = useState<LibraryShelf>("all")
  const [format, setFormat] = useState("")
  const [sort, setSort] = useState<LibrarySort>("newest")
  const [page, setPage] = useState(1)
  const [view, setView] = useState<"grid" | "list">("grid")
  const notebooksQuery = useQuery({ queryKey: ["notebooks", user?.id], queryFn: ({ signal }) => getNotebooks(signal), enabled: user != null, retry: 1 })
  const notebooks = notebooksQuery.data ?? []
  const notebook = notebooks.find((item) => item.id === notebookId)
  const moveToNotebook = notebooks.find((item) => item.id === moveToId)
  const { data, isPending, isFetching, isPlaceholderData, isError, refetch } = useQuery({
    queryKey: ["library", user?.id, scope, { search: debouncedSearch, shelf, format, sort, page }],
    queryFn: ({ signal }) => getLibrary({ search: debouncedSearch, shelf, format, sort, page, notebookId: filterNotebookId, unfiled: filterUnfiled }, signal),
    enabled: user != null,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[2] === scope ? previous : undefined,
    retry: 1,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => query.state.data?.processing_count ? 3000 : false,
  })

  useEffect(() => {
    useReader.getState().selectDoc(null)
    useReader.setState({ focusMode: false })
    const state = useReader.getState()
    if (!state.activePodcast && !state.activeReading) {
      ttsEngine.stop()
    }
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => { setDebouncedSearch(search); setPage(1) }, 250)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    setSearch(""); setDebouncedSearch(""); setShelf("all"); setFormat(""); setPage(1); setSelected([]); setActionError("")
  }, [notebookParam, unfiled, moveToId])

  useEffect(() => { setSourceNotebook(""); setSelected([]) }, [moveToId])

  useEffect(() => {
    if (home) { setSearch(""); setDebouncedSearch(""); setShelf("all"); setFormat(""); setPage(1); setSelected([]) }
  }, [home])

  const changeShelf = (next: LibraryShelf) => {
    if (home || (!notebookId && !unfiled && !picking)) router.push("/dashboard?view=documents")
    setShelf(next); setPage(1); setSelected([])
  }
  const openUpload = () => useReader.getState().setUploadModalOpen(true)
  const refreshLibrary = () => {
    void queryClient.invalidateQueries({ queryKey: ["library"] })
    void queryClient.invalidateQueries({ queryKey: ["documents"] })
    void queryClient.invalidateQueries({ queryKey: ["document"] })
    void queryClient.invalidateQueries({ queryKey: ["notebooks"] })
  }
  const clearFilters = () => {
    setSearch(""); setDebouncedSearch(""); setShelf("all"); setFormat(""); setPage(1)
  }
  const counts: Partial<Record<LibraryShelf, number>> = data ? {
    all: data.library_total, reading: data.reading_count,
    unread: data.library_total - data.reading_count,
    processing: data.processing_count, failed: data.failed_count,
  } : {}
  const toggleSelected = (id: number) => setSelected((previous) => previous.includes(id) ? previous.filter((item) => item !== id) : previous.length < 200 ? [...previous, id] : previous)
  const openExisting = () => { setSelected([]); router.push(`/dashboard?view=documents&move_to=${notebookId}`) }
  const finishMove = () => { setMoveOpen(false); setSelected([]); refreshLibrary() }
  const addToNotebook = async () => {
    if (!moveToNotebook || moving || !selected.length) return
    setMoving(true); setActionError("")
    try { await moveDocuments(selected, moveToNotebook.id); finishMove(); router.push(`/dashboard?notebook=${moveToNotebook.id}`) }
    catch (error) { setActionError(notebookError(error)) }
    finally { setMoving(false) }
  }
  const filtered = Boolean(debouncedSearch || shelf !== "all" || format)
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / LIBRARY_PAGE_SIZE))

  useEffect(() => {
    if (data && !isPlaceholderData && page > totalPages) setPage(totalPages)
  }, [data, isPlaceholderData, page, totalPages])

  const invalidScope = (notebookParam != null && unfiled) || (moveToParam != null && (notebookParam != null || unfiled)) || (notebookParam != null && (!Number.isSafeInteger(notebookId) || Number(notebookId) < 1)) || (moveToParam != null && (!Number.isSafeInteger(moveToId) || Number(moveToId) < 1))
  const missingScope = !notebooksQuery.isPending && !notebooksQuery.isError && ((notebookId != null && !notebook) || (moveToId != null && !moveToNotebook))
  if (invalidScope || missingScope) return <div className="library-shell"><TopBar library /><main className="library-main"><div className="library-empty" role="alert"><AlertCircle size={32} /><h1>This notebook is unavailable</h1><p>It may have been deleted. Return to your notebooks to continue.</p><Link href="/dashboard" className="library-add-button">Your notebooks</Link></div></main></div>

  return (
    <div className="library-shell">
      <TopBar library />
      <div className="library-layout">
        <aside className="library-navigation" aria-label="Library shelves">
          <p className="library-nav-label">Your library</p>
          <nav><Link href="/dashboard" className={`library-nav-item ${home ? "is-active" : ""}`}><FolderOpen size={17} /><span>Notebooks</span></Link><Link href="/dashboard/shared" className="library-nav-item"><UsersRound size={17} /><span>Shared library</span></Link><Link href="/dashboard?view=podcasts" className={`library-nav-item ${isPodcastsView ? "is-active" : ""}`}><Headphones size={17} /><span>Podcasts</span></Link><Link href="/dashboard?unfiled=true" className={`library-nav-item ${unfiled ? "is-active" : ""}`}><FileText size={17} /><span>Unfiled</span></Link>{SHELVES.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" className={`library-nav-item ${!home && !unfiled && !notebookId && !isPodcastsView && shelf === id ? "is-active" : ""}`} onClick={() => changeShelf(id)} aria-pressed={!home && !unfiled && !notebookId && !isPodcastsView && shelf === id}>
              <Icon size={17} strokeWidth={1.7} aria-hidden="true" /><span>{label}</span>
              {counts[id] != null && <span className="library-nav-count">{counts[id]}</span>}
            </button>
          ))}</nav>
          <div className="library-storage-note"><FolderOpen size={19} strokeWidth={1.5} /><p>A place for everything<br />you want to read.</p></div>
        </aside>
        <main className="library-main" id="library-main">
          <div className="library-page-heading">
            <div>{!home && <Link href="/dashboard" className="notebook-breadcrumb">Your notebooks</Link>}<h1>{notebook?.name || (unfiled ? "Unfiled" : isPodcastsView ? "Podcasts" : "Your library")}</h1><p>{notebook?.description || (isPodcastsView ? "Generated audio discussions across all your documents." : home ? "A notebook for every topic. Keep related documents together." : unfiled ? "Choose documents and move them into a notebook." : "Pick up where you left off, or find your next read.")}</p></div>
            {!isPodcastsView && <div className="library-heading-actions">{home ? <button type="button" className="library-add-button" onClick={() => setEditingNotebook(null)}><Plus size={18} />New notebook</button> : notebook && <button type="button" className="library-secondary-button" onClick={() => setEditingNotebook(notebook)}>Edit notebook</button>}<button type="button" className={home ? "library-secondary-button" : "library-add-button"} onClick={openUpload}><Plus size={18} />Add documents</button></div>}
          </div>
          {home && <NotebookShelf notebooks={notebooks} unfiledCount={data ? Math.max(0, data.library_total - notebooks.reduce((sum, item) => sum + item.document_count, 0)) : null} loading={notebooksQuery.isPending} error={notebooksQuery.isError} onCreate={() => setEditingNotebook(null)} onEdit={setEditingNotebook} onRetry={() => void notebooksQuery.refetch()} />}
          {home && isError && <div className="notebook-load-error" role="alert">Document counts could not be loaded.<button type="button" onClick={() => void refetch()}>Try again</button></div>}
          {!picking && !filtered && page === 1 && Boolean(data?.recent.length) && (
            <section className="library-recent" aria-labelledby="recent-reading-heading">
              <div className="library-section-heading"><h2 id="recent-reading-heading">Continue reading</h2><button type="button" onClick={() => { changeShelf("reading"); setSort("recent") }}>See all</button></div>
              <div className="library-recent-grid">{data?.recent.map((document) => <DocumentCard key={document.id} document={document} compact />)}</div>
            </section>
          )}
          {!picking && (home || isPodcastsView) && <PodcastGallery />}
          {!home && !isPodcastsView && <section aria-labelledby="documents-heading" className="library-shelf">
            <div className="library-section-heading"><h2 id="documents-heading">{SHELVES.find((item) => item.id === shelf)?.label}</h2>{notebook && <div className="notebook-add-sources"><button type="button" onClick={openExisting}>Add existing documents</button><Link href={`/dashboard/shared?notebook=${notebook.id}`}>Browse shared library</Link></div>}<span className="library-result-count" aria-live="polite">{data ? `${data.total} ${data.total === 1 ? "document" : "documents"}` : "Loading library"}</span></div>
            {moveToNotebook && <div className="notebook-choose-banner"><p>Select documents to add to <strong>{moveToNotebook.name}</strong>. Documents will move out of their current notebook.</p><Link href={`/dashboard?notebook=${moveToNotebook.id}`}>Cancel</Link></div>}
            {(selected.length > 0 || moveToNotebook) && <div className="notebook-selection-bar"><span>{selected.length} selected</span><button type="button" disabled={isPlaceholderData} onClick={() => setSelected((previous) => Array.from(new Set([...previous, ...(data?.documents || []).filter((document) => !picking || document.notebook_id !== moveToId).map((document) => document.id)])).slice(0, 200))}>Select this page</button><button type="button" onClick={() => setSelected([])} disabled={!selected.length}>Clear selection</button><button type="button" className="library-add-button" disabled={!selected.length || moving} onClick={() => moveToNotebook ? void addToNotebook() : setMoveOpen(true)}>{moving ? "Moving…" : moveToNotebook ? `Add to ${moveToNotebook.name}` : "Move to notebook"}</button></div>}
            {actionError && <p role="alert" className="notebook-dialog-error">{actionError}</p>}
            <div className="library-toolbar">
              {picking && <select aria-label="Source notebook" value={sourceNotebook} onChange={(event) => { setSourceNotebook(event.target.value); setPage(1) }}><option value="">All notebooks</option><option value="unfiled">Unfiled</option>{notebooks.filter((item) => item.id !== moveToId).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
              <div className="library-search"><Search size={17} aria-hidden="true" /><input aria-label="Search documents" type="search" placeholder="Search your documents" value={search} onChange={(event) => setSearch(event.target.value)} />{isFetching && <Loader2 size={15} className="animate-spin" aria-label="Updating library" />}</div>
              <select aria-label="Document format" value={format} onChange={(event) => { setFormat(event.target.value); setPage(1) }}><option value="">All formats</option>{Object.keys(data?.formats || {}).sort().map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}</select>
              <select aria-label="Sort documents" value={sort} onChange={(event) => { setSort(event.target.value as LibrarySort); setPage(1) }}><option value="newest">Newest first</option><option value="recent">Last read</option><option value="title">Title A-Z</option></select>
              <div className="library-view-switch" role="group" aria-label="Document view"><button type="button" aria-label="Gallery view" aria-pressed={view === "grid"} onClick={() => setView("grid")}><LayoutGrid size={17} /></button><button type="button" aria-label="List view" aria-pressed={view === "list"} onClick={() => setView("list")}><List size={18} /></button></div>
            </div>
            {filtered && <div className="library-filter-summary"><span>{debouncedSearch ? `Results for "${debouncedSearch}"` : "Filtered library"}{format ? ` in ${format.toUpperCase()}` : ""}</span><button type="button" onClick={clearFilters}><X size={13} />Clear filters</button></div>}
            {isPending ? (
              <div className="library-document-grid" aria-label="Loading documents">{Array.from({ length: 8 }, (_, index) => <div key={index} className="library-card-skeleton"><div /><span /><span /></div>)}</div>
            ) : isError ? (
              <div className="library-empty" role="alert"><AlertCircle size={32} /><h3>Your library could not be loaded</h3><p>Check your connection and try again.</p><button type="button" className="library-add-button" onClick={() => void refetch()}>Try again</button></div>
            ) : data?.library_total === 0 ? (
              <div className="library-empty"><BookOpen size={42} strokeWidth={1.3} /><h3>{notebook ? "This notebook is ready for its first documents" : "Make room for your next idea"}</h3><p>Add a PDF, EPUB, or document to start reading,<br className="hidden sm:block" /> taking notes, and asking questions.</p><button type="button" className="library-add-button" onClick={openUpload}><Plus size={17} />Add your first document</button>{notebook && <><button type="button" className="library-secondary-button" onClick={openExisting}>Add existing documents</button><Link href={`/dashboard/shared?notebook=${notebook.id}`} className="shared-notebook-link">Browse shared library</Link></>}<span className="library-empty-formats">PDF, EPUB, DOCX, PPTX, TXT and Markdown</span></div>
            ) : data?.documents.length === 0 ? (
              <div className="library-empty"><FileText size={34} strokeWidth={1.4} /><h3>No documents found</h3><p>Try a different title or clear your filters.</p><button type="button" className="library-secondary-button" onClick={clearFilters}>Show all documents</button></div>
            ) : (
              <div className={`library-document-grid ${view === "list" ? "library-list-view" : ""}`} aria-busy={isPlaceholderData}>{data?.documents.map((document) => <DocumentCard key={document.id} document={document} selectionMode={picking || selected.length > 0} selectionDisabled={picking && document.notebook_id === moveToId} selected={selected.includes(document.id)} onSelect={() => toggleSelected(document.id)} notebookName={!notebook ? notebooks.find((item) => item.id === document.notebook_id)?.name : undefined} />)}</div>
            )}
            {data && data.total > LIBRARY_PAGE_SIZE && <nav className="library-pagination" aria-label="Library pages"><span>Page {page} of {totalPages}</span><div><button type="button" aria-label="Previous page" disabled={page === 1 || isPlaceholderData} onClick={() => setPage((current) => current - 1)}><ChevronLeft size={17} />Previous</button><button type="button" aria-label="Next page" disabled={page >= totalPages || isPlaceholderData} onClick={() => setPage((current) => current + 1)}>Next<ChevronRight size={17} /></button></div></nav>}
          </section>}
          {home && <Link href="/dashboard?view=documents" className="notebook-all-documents">Browse all documents</Link>}
        </main>
      </div>
      <UploadModal onUploaded={refreshLibrary} notebookId={notebookId} notebookName={notebook?.name} />
      {editingNotebook !== undefined && <NotebookDialog notebook={editingNotebook} onClose={() => setEditingNotebook(undefined)} onSaved={(saved) => { setEditingNotebook(undefined); refreshLibrary(); router.push(`/dashboard?notebook=${saved.id}`) }} onDeleted={() => { setEditingNotebook(undefined); refreshLibrary(); router.push("/dashboard") }} />}
      {moveOpen && <MoveDocumentsDialog excludeNotebookId={notebookId} documentIds={selected} notebooks={notebooks} onClose={() => setMoveOpen(false)} onMoved={finishMove} />}
    </div>
  )
}
