"use client"

import { useMemo } from "react"
import { useWebSocket } from "./useWebSocket"

export function useDocumentProgress(docId: number | null) {
  const progress = useWebSocket(docId || undefined)

  // Derive status from progress without calling setState during render
  return useMemo(() => {
    if (!docId) return { status: "idle" as const, progress: 0 }
    if (!progress) return { status: "idle" as const, progress: 0 }

    return { status: progress.task, progress: progress.value }
  }, [docId, progress])
}
