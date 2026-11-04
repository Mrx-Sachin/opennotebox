"use client"

import { useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { isAxiosError } from "axios"
import { Loader2, X } from "lucide-react"
import type { Notebook } from "@/services/notebookApi"
import type { SharedDocument } from "@/services/types"
import { addSharedDocuments } from "@/services/sharedLibraryApi"

function errorMessage(error: unknown) {
  if (isAxiosError(error) && typeof error.response?.data?.detail === "string") {
    return error.response.data.detail
  }
  return "The documents could not be added. Please try again."
}

export default function SharedAddDialog({
  documents,
  notebooks,
  defaultNotebookId,
  unfiledName = "Unfiled",
  onClose,
  onAdded,
}: {
  documents: SharedDocument[]
  notebooks: Notebook[]
  defaultNotebookId: number | null
  unfiledName?: string
  onClose: () => void
  onAdded: (result: { added: number; alreadyAdded: number }) => void
}) {
  const [destination, setDestination] = useState(defaultNotebookId ? String(defaultNotebookId) : "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const add = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || documents.length === 0) return
    setBusy(true)
    setError("")
    try {
      const notebookId = destination ? Number(destination) : null
      const result = await addSharedDocuments(documents.map((document) => document.id), notebookId)
      onAdded({ added: result.added, alreadyAdded: result.already_added })
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="notebook-dialog-overlay" />
        <Dialog.Content className="notebook-dialog shared-add-dialog">
          <div className="notebook-dialog-heading">
            <Dialog.Title>Add to my notebook</Dialog.Title>
            <Dialog.Close disabled={busy} aria-label="Close add dialog"><X size={19} /></Dialog.Close>
          </div>
          <Dialog.Description>
            Add {documents.length === 1 ? "this paper" : `${documents.length} papers`} to your library. Your notes and reading progress stay private.
          </Dialog.Description>
          <form onSubmit={(event) => void add(event)}>
            <label htmlFor="shared-destination">Choose a destination</label>
            <select id="shared-destination" value={destination} onChange={(event) => setDestination(event.target.value)} disabled={busy}>
              <option value="">{unfiledName}</option>
              {notebooks.map((notebook) => <option key={notebook.id} value={notebook.id}>{notebook.name}</option>)}
            </select>
            {error && <p role="alert" className="notebook-dialog-error">{error}</p>}
            <div className="notebook-dialog-actions">
              <Dialog.Close className="library-secondary-button" disabled={busy}>Cancel</Dialog.Close>
              <button type="submit" className="library-add-button" disabled={busy || documents.length === 0}>
                {busy && <Loader2 size={15} className="animate-spin" />}
                Add to my notebook
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
