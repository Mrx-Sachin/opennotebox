import api from "./api"

export interface Notebook {
  id: number
  name: string
  description: string | null
  document_count: number
  created_at: string
  updated_at: string
}

export function getNotebooks(signal?: AbortSignal) {
  return api.get<Notebook[]>("/api/notebooks", { signal }).then((response) => response.data)
}

export function saveNotebook(notebookId: number | null, name: string, description: string) {
  const body = { name, description }
  return (notebookId == null
    ? api.post<Notebook>("/api/notebooks", body)
    : api.patch<Notebook>(`/api/notebooks/${notebookId}`, body)
  ).then((response) => response.data)
}

export function deleteNotebook(notebookId: number) {
  return api.delete(`/api/notebooks/${notebookId}`)
}

export function moveDocuments(documentIds: number[], notebookId: number | null) {
  return api.post<{ moved: number }>("/api/notebooks/move-documents", {
    document_ids: documentIds,
    notebook_id: notebookId,
  }).then((response) => response.data)
}
