import Link from "next/link"
import { ArrowUpRight, Clock3, Loader2, AlertCircle } from "lucide-react"
import type { Document } from "@/services/types"
import { isDocumentProcessing } from "@/hooks/useDocuments"
import DocumentCover, { displayTitle } from "./DocumentCover"

function readingProgress(document: Document) {
  if (!document.last_read_page || !document.num_pages) return 0
  return Math.min(100, Math.max(1, Math.round(document.last_read_page / document.num_pages * 100)))
}

export default function DocumentCard({ document, compact = false, selected = false, onSelect, notebookName, selectionMode = false, selectionDisabled = false }: { document: Document; compact?: boolean; selected?: boolean; onSelect?: () => void; notebookName?: string; selectionMode?: boolean; selectionDisabled?: boolean }) {
  const progress = readingProgress(document)
  const processing = isDocumentProcessing(document)
  const failed = document.processing_status === "failed"
  const content = <>
      <DocumentCover document={document} />
      <div className="library-document-info">
        <div className="library-document-heading">
          <span className="library-document-format">{document.format.toUpperCase()}</span>
          <ArrowUpRight size={16} className="library-document-arrow" aria-hidden="true" />
        </div>
        <h3 title={document.title}>{displayTitle(document.title)}</h3>
        {selectionDisabled && <span className="document-notebook-name">Already in this notebook</span>}
        {notebookName && <span className="document-notebook-name">{notebookName}</span>}
        <p className="library-document-meta">
          {document.num_pages > 0 ? `${document.num_pages.toLocaleString()} ${document.format === "epub" ? "sections" : "pages"}` : "Preparing document"}
        </p>
        {failed ? (
          <span className="library-document-status library-status-failed"><AlertCircle size={13} />Processing failed</span>
        ) : processing ? (
          <span className="library-document-status"><Loader2 size={13} className="animate-spin" />{document.processing_status === "pending" ? "Queued" : "Processing"}</span>
        ) : progress > 0 ? (
          <div className="library-progress">
            <div className="library-progress-label"><span>Page {document.last_read_page}</span><span>{progress}%</span></div>
            <div className="library-progress-track" role="progressbar" aria-label={`Reading progress for ${displayTitle(document.title)}`} aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${progress}%` }} /></div>
          </div>
        ) : (
          <span className="library-document-status library-status-unread"><Clock3 size={12} />Not started</span>
        )}
      </div>
    </>
  return (
    <div className={`library-document-entry ${selected ? "is-selected" : ""}`}>
    {selectionMode && onSelect ? <label className={`library-document document-picker-card ${selectionDisabled ? "is-disabled" : ""}`}>
      {content}
      <span className="document-select"><input type="checkbox" aria-label={`Select ${displayTitle(document.title)}`} checked={selected} disabled={selectionDisabled} onChange={onSelect} /></span>
    </label> : <Link href={`/dashboard/read/${document.id}`} className={`library-document ${compact ? "library-document-recent" : ""}`} aria-label={`Open ${displayTitle(document.title)}`}>{content}</Link>}
    {selectionMode && <Link href={`/dashboard/read/${document.id}`} target="_blank" rel="noopener noreferrer" className="document-preview-link" aria-label={`Preview ${displayTitle(document.title)} in a new tab`}>Preview in new tab</Link>}
    {!selectionMode && onSelect && <label className="document-select"><input type="checkbox" aria-label={`Select ${displayTitle(document.title)}`} checked={selected} onChange={onSelect} /></label>}
    </div>
  )
}
