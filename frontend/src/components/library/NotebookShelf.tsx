"use client"

import Link from "next/link"
import { Folder, FolderOpen, MoreHorizontal, Plus } from "lucide-react"
import type { CSSProperties } from "react"
import type { Notebook } from "@/services/notebookApi"

const COLORS = ["#b6a5df", "#92bfb4", "#c7ab84", "#98b2d2", "#d2a2b9", "#b8bd89"]

export default function NotebookShelf({
  notebooks,
  unfiledName = "Unfiled",
  unfiledDescription = "Documents waiting for a notebook.",
  unfiledCount,
  loading,
  error,
  onCreate,
  onEdit,
  onEditUnfiled,
  onRetry,
}: {
  notebooks: Notebook[]
  unfiledName?: string
  unfiledDescription?: string
  unfiledCount: number | null
  loading: boolean
  error: boolean
  onCreate: () => void
  onEdit: (notebook: Notebook) => void
  onEditUnfiled?: () => void
  onRetry: () => void
}) {
  return (
    <section className="notebook-shelf" aria-labelledby="notebooks-heading">
      <div className="library-section-heading">
        <h2 id="notebooks-heading">Your notebooks</h2>
        <span className="library-result-count">{notebooks.length} {notebooks.length === 1 ? "notebook" : "notebooks"}</span>
      </div>
      {error ? (
        <div className="notebook-load-error" role="alert">
          Notebooks could not be loaded.
          <button type="button" onClick={onRetry}>Try again</button>
        </div>
      ) : (
        <div className="notebook-grid" aria-busy={loading}>
          {notebooks.map((notebook) => (
            <div className="notebook-card" key={notebook.id} style={{ "--notebook-accent": COLORS[notebook.id % COLORS.length] } as CSSProperties}>
              <Link href={`/dashboard?notebook=${notebook.id}`} aria-label={`Open notebook ${notebook.name}`}>
                <div className="notebook-folder"><Folder size={40} strokeWidth={1.2} /><span /><span /></div>
                <h3>{notebook.name}</h3>
                <p>{notebook.description || "Related documents, together."}</p>
                <span className="notebook-source-count">{notebook.document_count} {notebook.document_count === 1 ? "document" : "documents"}</span>
              </Link>
              <button className="notebook-edit" type="button" aria-label={`Edit notebook ${notebook.name}`} onClick={() => onEdit(notebook)}>
                <MoreHorizontal size={19} />
              </button>
            </div>
          ))}
          <div className="notebook-card notebook-unfiled">
            <Link href="/dashboard?unfiled=true" aria-label={`Open ${unfiledName} documents`}>
              <FolderOpen size={34} strokeWidth={1.3} />
              <h3>{unfiledName}</h3>
              <p>{unfiledDescription}</p>
              <span className="notebook-source-count">
                {unfiledCount == null ? "Loading documents" : `${unfiledCount} ${unfiledCount === 1 ? "document" : "documents"}`}
              </span>
            </Link>
            {onEditUnfiled && (
              <button
                className="notebook-edit"
                type="button"
                aria-label={`Edit ${unfiledName}`}
                onClick={onEditUnfiled}
              >
                <MoreHorizontal size={19} />
              </button>
            )}
          </div>
          <button type="button" className="notebook-create-card" onClick={onCreate}>
            <Plus size={24} />
            <span>Create a notebook</span>
            <small>Start with a topic or project</small>
          </button>
        </div>
      )}
    </section>
  )
}
