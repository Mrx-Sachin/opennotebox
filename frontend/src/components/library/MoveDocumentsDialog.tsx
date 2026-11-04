"use client"

import { useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { Loader2, X } from "lucide-react"
import { moveDocuments, type Notebook } from "@/services/notebookApi"
import { notebookError } from "./NotebookDialog"

export default function MoveDocumentsDialog({
  documentIds,
  notebooks,
  unfiledName = "Unfiled",
  onClose,
  onMoved,
  initialNotebookId,
  excludeNotebookId,
}: {
  documentIds: number[]
  notebooks: Notebook[]
  unfiledName?: string
  initialNotebookId?: number | null
  excludeNotebookId?: number | null
  onClose: () => void
  onMoved: () => void
}) {
  const [destination, setDestination] = useState(String(initialNotebookId ?? notebooks.find((notebook) => notebook.id !== excludeNotebookId)?.id ?? ""))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const move = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError("")
    try { await moveDocuments(documentIds, destination ? Number(destination) : null); onMoved() }
    catch (caught) { setError(notebookError(caught)); setBusy(false) }
  }
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="notebook-dialog-overlay" />
        <Dialog.Content className="notebook-dialog">
          <div className="notebook-dialog-heading">
            <Dialog.Title>Move documents</Dialog.Title>
            <Dialog.Close disabled={busy} aria-label="Close move dialog"><X size={19} /></Dialog.Close>
          </div>
          <Dialog.Description>Choose a notebook for {documentIds.length} selected {documentIds.length === 1 ? "document" : "documents"}.</Dialog.Description>
          <form onSubmit={move}>
            <label htmlFor="notebook-destination">Destination</label>
            <select id="notebook-destination" value={destination} onChange={(event) => setDestination(event.target.value)} disabled={busy}>
              <option value="">{unfiledName}</option>
              {notebooks.filter((notebook) => notebook.id !== excludeNotebookId).map((notebook) => <option key={notebook.id} value={notebook.id}>{notebook.name}</option>)}
            </select>
            {error && <p role="alert" className="notebook-dialog-error">{error}</p>}
            <div className="notebook-dialog-actions">
              <button type="submit" className="library-add-button" disabled={busy}>
                {busy && <Loader2 size={15} className="animate-spin" />}
                Move documents
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
