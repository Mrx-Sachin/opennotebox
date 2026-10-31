"use client"

import { useQuery } from "@tanstack/react-query"
import api from "@/services/api"
import type { Document } from "@/services/types"

const FINISHED = new Set(["completed", "extracted", "summarizing_pages", "summarizing_sections", "failed"])

export function isDocumentProcessing(document: Document) {
  return !FINISHED.has(document.processing_status)
}

export function useDocuments(userId?: number, notebookId?: number | null, ready = true) {
  return useQuery({
    queryKey: ["documents", userId, notebookId],
    enabled: userId != null && ready,
    queryFn: ({ signal }) => api.get<Document[]>("/api/documents", {
      signal, params: { limit: 200, notebook_id: notebookId || undefined, unfiled: notebookId === null || undefined },
    }).then((response) => response.data),
    refetchOnWindowFocus: true,
    refetchInterval: (query) => query.state.data?.some(isDocumentProcessing) ? 8000 : false,
  })
}
