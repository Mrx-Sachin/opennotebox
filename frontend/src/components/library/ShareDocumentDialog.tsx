"use client"

import { useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { isAxiosError } from "axios"
import { Loader2, X } from "lucide-react"
import type { Document, SharedDocumentUpdate } from "@/services/types"
import { updateSharedDocument } from "@/services/sharedLibraryApi"

const CATEGORIES = ["paper", "textbook", "manual", "report", "tutorial", "reference"]

function errorMessage(error: unknown) {
  if (isAxiosError(error) && typeof error.response?.data?.detail === "string") {
    return error.response.data.detail
  }
  return "The team listing could not be updated. Please try again."
}

export default function ShareDocumentDialog({ document, onClose, onUpdated }: {
  document: Document
  onClose: () => void
  onUpdated: (update: SharedDocumentUpdate) => void
}) {
  const initialCategory = document.document_type || "paper"
  const initialIsOther = !CATEGORIES.includes(initialCategory)
  const [title, setTitle] = useState(document.shared_title || document.title.replace(/\.pdf$/i, ""))
  const [author, setAuthor] = useState(document.author || "")
  const [description, setDescription] = useState(document.description || "")
  const [topics, setTopics] = useState((document.topics || []).join(", "))
  const [category, setCategory] = useState(initialIsOther ? "other" : initialCategory)
  const [otherCategory, setOtherCategory] = useState(initialIsOther ? initialCategory : "")
  const [language, setLanguage] = useState(document.language || "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || !title.trim()) return
    const update: SharedDocumentUpdate = {
      is_shared: true,
      title: title.trim(),
      author: author.trim(),
      description: description.trim(),
      topics: topics.split(",").map((topic) => topic.trim()).filter(Boolean),
      document_type: category === "other" ? otherCategory.trim() : category,
      language: language.trim(),
    }
    if (!update.document_type) {
      setError("Choose a document type or enter one.")
      return
    }
    setBusy(true)
    setError("")
    try {
      await updateSharedDocument(document.id, update)
      onUpdated(update)
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  const unshare = async () => {
    if (busy || !document.is_shared) return
    const update: SharedDocumentUpdate = { is_shared: false }
    setBusy(true)
    setError("")
    try {
      await updateSharedDocument(document.id, update)
      onUpdated(update)
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="notebook-dialog-overlay" />
        <Dialog.Content className="notebook-dialog shared-manage-dialog">
          <div className="notebook-dialog-heading">
            <Dialog.Title>{document.is_shared ? "Edit team listing" : "Share with team"}</Dialog.Title>
            <Dialog.Close disabled={busy} aria-label="Close sharing dialog"><X size={19} /></Dialog.Close>
          </div>
          <Dialog.Description>
            Share this PDF in the team library. People who have already added it keep access if you unshare it. Your private notes and chats are never shared.
          </Dialog.Description>
          <form onSubmit={(event) => void save(event)}>
            <label htmlFor="shared-title">Title</label>
            <input id="shared-title" maxLength={240} required value={title} onChange={(event) => setTitle(event.target.value)} disabled={busy} />
            <label htmlFor="shared-author">Author <span>(optional)</span></label>
            <input id="shared-author" maxLength={160} value={author} onChange={(event) => setAuthor(event.target.value)} disabled={busy} />
            <label htmlFor="shared-topics">Topics <span>(comma separated)</span></label>
            <input id="shared-topics" maxLength={500} value={topics} onChange={(event) => setTopics(event.target.value)} placeholder="research, design, systems" disabled={busy} />
            <label htmlFor="shared-description">Description <span>(optional)</span></label>
            <textarea id="shared-description" rows={3} maxLength={1000} value={description} onChange={(event) => setDescription(event.target.value)} disabled={busy} />
            <div className="shared-form-pair">
              <div>
                <label htmlFor="shared-category">Document type</label>
                <select id="shared-category" value={category} onChange={(event) => setCategory(event.target.value)} disabled={busy}>
                  {CATEGORIES.map((item) => <option key={item} value={item}>{item[0].toUpperCase() + item.slice(1)}</option>)}
                  <option value="other">Other</option>
                </select>
                {category === "other" && <input aria-label="Other document type" maxLength={80} value={otherCategory} onChange={(event) => setOtherCategory(event.target.value)} placeholder="Document type" disabled={busy} />}
              </div>
              <div>
                <label htmlFor="shared-language">Language <span>(optional)</span></label>
                <input id="shared-language" maxLength={40} value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="English" disabled={busy} />
              </div>
            </div>
            {error && <p role="alert" className="notebook-dialog-error">{error}</p>}
            <div className="notebook-dialog-actions">
              {document.is_shared && <button type="button" className="notebook-delete-button" disabled={busy} onClick={() => void unshare()}>Stop sharing</button>}
              <Dialog.Close className="library-secondary-button" disabled={busy}>Cancel</Dialog.Close>
              <button type="submit" className="library-add-button" disabled={busy || !title.trim() || (category === "other" && !otherCategory.trim())}>
                {busy && <Loader2 size={15} className="animate-spin" />}
                {document.is_shared ? "Save listing" : "Share with team"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
