"use client"

import { useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { isAxiosError } from "axios"
import { Loader2, X } from "lucide-react"
import { deleteNotebook, saveNotebook, type Notebook } from "@/services/notebookApi"

export function notebookError(error: unknown) {
  if (isAxiosError(error) && typeof error.response?.data?.detail === "string") return error.response.data.detail
  return "The change could not be saved. Please try again."
}

export default function NotebookDialog({ notebook, onClose, onSaved, onDeleted }: {
  notebook: Notebook | null
  onClose: () => void
  onSaved: (notebook: Notebook) => void
  onDeleted: () => void
}) {
  const [name, setName] = useState(notebook?.name || "")
  const [description, setDescription] = useState(notebook?.description || "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [confirmDelete, setConfirmDelete] = useState(false)

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || !name.trim()) return
    setBusy(true); setError("")
    try { onSaved(await saveNotebook(notebook?.id ?? null, name.trim(), description.trim())) }
    catch (error) { setError(notebookError(error)); setBusy(false) }
  }
  const remove = async () => {
    if (!notebook || busy) return
    if (!confirmDelete) { setConfirmDelete(true); return }
    setBusy(true); setError("")
    try { await deleteNotebook(notebook.id); onDeleted() }
    catch (error) { setError(notebookError(error)); setBusy(false) }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="notebook-dialog-overlay" />
        <Dialog.Content className="notebook-dialog">
          <div className="notebook-dialog-heading"><Dialog.Title>{notebook ? "Edit notebook" : "New notebook"}</Dialog.Title><Dialog.Close disabled={busy} aria-label="Close notebook dialog"><X size={19} /></Dialog.Close></div>
          <Dialog.Description>Keep related PDFs and documents together around a topic or project.</Dialog.Description>
          <form onSubmit={save}>
            <label htmlFor="notebook-name">Notebook name</label><input id="notebook-name" autoComplete="off" maxLength={100} required value={name} onChange={(event) => setName(event.target.value)} placeholder="For example, Machine learning" disabled={busy} />
            <label htmlFor="notebook-description">Description <span>(optional)</span></label><textarea id="notebook-description" maxLength={240} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What belongs in this notebook?" disabled={busy} />
            {error && <p role="alert" className="notebook-dialog-error">{error}</p>}
            {confirmDelete && <p className="notebook-delete-message">Documents will return to Unfiled. Their files, notes, and reading progress will be kept.</p>}
            <div className="notebook-dialog-actions">
              {notebook && <button type="button" className="notebook-delete-button" disabled={busy} onClick={() => void remove()}>{confirmDelete ? "Confirm delete notebook" : "Delete notebook"}</button>}
              <button type="submit" className="library-add-button" disabled={busy || !name.trim()}>{busy && <Loader2 size={15} className="animate-spin" />}{notebook ? "Save notebook" : "Create notebook"}</button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
