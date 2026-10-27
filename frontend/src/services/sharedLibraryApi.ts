import api from "./api"
import type { Document, SharedDocumentUpdate, SharedLibraryResult } from "./types"

export interface SharedLibraryFilters {
  search: string
  topic: string
  documentType: string
  notebookId?: number
  limit: number
  offset: number
}

export function getSharedLibrary(filters: SharedLibraryFilters, signal?: AbortSignal) {
  return api.get<SharedLibraryResult>("/api/shared-library", {
    signal,
    params: {
      search: filters.search || undefined,
      topic: filters.topic || undefined,
      document_type: filters.documentType || undefined,
      notebook_id: filters.notebookId,
      limit: filters.limit,
      offset: filters.offset,
    },
  }).then((response) => response.data)
}

export function addSharedDocuments(documentIds: number[], notebookId: number | null) {
  return api.post<{ added: number; already_added: number; document_ids: number[] }>(
    "/api/shared-library/add",
    { document_ids: documentIds, notebook_id: notebookId },
  ).then((response) => response.data)
}

export function updateSharedDocument(documentId: number, update: SharedDocumentUpdate) {
  return api.patch<Document>(`/api/shared-library/${documentId}`, update).then((response) => response.data)
}
