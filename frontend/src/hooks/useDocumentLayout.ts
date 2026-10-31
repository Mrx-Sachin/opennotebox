"use client"

import { useCallback, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { analyzeDocumentLayout, getDocumentLayout } from "@/services/api"
import { useAuth } from "@/stores/auth"
import type { DocumentLayoutStatus } from "@/services/types"

// Keep a request shared across viewer unmounts while its POST is in flight.
const pendingRequests = new Map<string, Promise<DocumentLayoutStatus>>()

export function useDocumentLayout(docId: number) {
  const userId = useAuth((state) => state.user?.id)
  const queryClient = useQueryClient()
  const queryKey = ["document-layout", userId, docId] as const
  const requestKey = `${userId}:${docId}`
  const pendingRef = useRef(false)
  const [requesting, setRequesting] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => pendingRequests.get(requestKey) ?? getDocumentLayout(docId, signal),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (current) => current.state.data?.status === "processing" ? 2000 : false,
  })

  const request = useCallback(async () => {
    if (pendingRef.current) return
    const key = ["document-layout", userId, docId] as const
    const current = queryClient.getQueryData<DocumentLayoutStatus>(key)
    if (!current || current.status === "ready" || current.status === "processing") return
    pendingRef.current = true
    setRequesting(true)
    setRequestError(null)
    try {
      // Prevent an older status response from overwriting the accepted request.
      let pending = pendingRequests.get(requestKey)
      if (!pending) {
        pending = (async () => {
          await queryClient.cancelQueries({ queryKey: key })
          return analyzeDocumentLayout(docId)
        })()
        pendingRequests.set(requestKey, pending)
      }
      const result = await pending
      queryClient.setQueryData(key, result)
    } catch {
      setRequestError("Could not start layout analysis. Please retry.")
    } finally {
      pendingRequests.delete(requestKey)
      pendingRef.current = false
      setRequesting(false)
    }
  }, [docId, userId, requestKey, queryClient])

  return {
    status: query.data?.status,
    error: requestError ?? (query.isError ? "Could not check layout analysis. Please retry." : query.data?.error),
    requesting,
    checking: query.isPending,
    request,
    retry: query.isError ? query.refetch : request,
  }
}
