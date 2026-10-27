import api from "./api"
import type { Document } from "./types"

export type LibraryShelf = "all" | "reading" | "unread" | "processing" | "failed"
export type LibrarySort = "recent" | "newest" | "title"

export interface LibraryFilters {
  search: string
  shelf: LibraryShelf
  format: string
  sort: LibrarySort
  page: number
  notebookId?: number | null
  unfiled?: boolean
}

export interface DocumentLibrary {
  documents: Document[]
  recent: Document[]
  total: number
  library_total: number
  reading_count: number
  processing_count: number
  failed_count: number
  formats: Record<string, number>
}

export const LIBRARY_PAGE_SIZE = 24

export function getLibrary(filters: LibraryFilters, signal?: AbortSignal) {
  return api.get<DocumentLibrary>("/api/documents/library", {
    signal,
    params: {
      notebook_id: filters.notebookId || undefined,
      unfiled: filters.unfiled || undefined,
      search: filters.search || undefined,
      shelf: filters.shelf,
      format: filters.format || undefined,
      sort: filters.sort,
      limit: LIBRARY_PAGE_SIZE,
      offset: (filters.page - 1) * LIBRARY_PAGE_SIZE,
    },
  }).then((response) => response.data)
}

export class PreviewPending extends Error {}

export async function getDocumentPreview(docId: number, signal?: AbortSignal): Promise<Blob | null> {
  const response = await api.get<Blob>(`/api/documents/${docId}/preview`, {
    signal,
    responseType: "blob",
  })
  if (response.status === 202) throw new PreviewPending("Cover is being prepared")
  if (response.status === 204) return null
  return response.data
}
