"use client"

import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { updateReadingProgress } from "@/services/api"
import { useReader } from "@/stores/reader"
import { useAuth } from "@/stores/auth"
import type { Document } from "@/services/types"

const DEBOUNCE_MS = 1500

interface PendingProgress {
  docId: number
  page: number
  revision: string
}

function progressKey(userId: number, docId: number) {
  return `onb.pendingProgress.${userId}.${docId}`
}

function readPending(userId: number, docId: number): PendingProgress | undefined {
  try {
    const raw = localStorage.getItem(progressKey(userId, docId))
    if (!raw) return
    const progress = JSON.parse(raw) as PendingProgress
    if (progress.docId === docId && Number.isSafeInteger(progress.page) && progress.page > 0 && typeof progress.revision === "string") {
      return progress
    }
  } catch {}
}

function storePending(userId: number, progress: PendingProgress) {
  try {
    localStorage.setItem(progressKey(userId, progress.docId), JSON.stringify(progress))
  } catch {}
}

function clearPending(userId: number, progress: PendingProgress) {
  try {
    if (readPending(userId, progress.docId)?.revision === progress.revision) {
      localStorage.removeItem(progressKey(userId, progress.docId))
    }
  } catch {}
}

export function useReadingProgress() {
  const queryClient = useQueryClient()
  const userId = useAuth((state) => state.user?.id)

  useEffect(() => {
    if (userId == null) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let pending: PendingProgress | undefined
    const requests = new Map<number, Promise<unknown>>()

    const patchCache = (docId: number, page: number) => {
      const patch = { last_read_page: page, last_read_at: new Date().toISOString() }
      queryClient.setQueryData<Document>(["document", userId, docId], (document) =>
        document ? { ...document, ...patch } : document
      )
      queryClient.setQueriesData<Document[]>({ queryKey: ["documents", userId] }, (documents) =>
        documents?.map((document) => document.id === docId ? { ...document, ...patch } : document)
      )
      void queryClient.invalidateQueries({ queryKey: ["library", userId], refetchType: "none" })
    }

    const flush = () => {
      clearTimeout(timer)
      if (!pending || useAuth.getState().user?.id !== userId) return
      const progress = pending
      pending = undefined
      const save = async () => {
        if (useAuth.getState().user?.id !== userId) return
        try {
          await updateReadingProgress(progress.docId, progress.page)
          clearPending(userId, progress)
        } catch {
          if (!pending && useAuth.getState().user?.id === userId) {
            pending = readPending(userId, progress.docId)
          }
        }
      }
      const previous = requests.get(progress.docId)
      const request = previous ? previous.then(save) : save()
      requests.set(progress.docId, request)
      void request.finally(() => {
        if (requests.get(progress.docId) === request) requests.delete(progress.docId)
      })
    }

    const capture = (docId: number, page: number) => {
      pending = { docId, page, revision: `${Date.now()}:${Math.random()}` }
      storePending(userId, pending)
      patchCache(docId, page)
      clearTimeout(timer)
      timer = setTimeout(flush, DEBOUNCE_MS)
    }

    const restorePending = () => {
      const state = useReader.getState()
      if (state.selectedDocId == null || state.progressRestoredFor === state.selectedDocId) return
      const progress = readPending(userId, state.selectedDocId)
      if (progress) {
        state.setPage(Math.min(progress.page, state.numPages || progress.page))
      }
    }

    const unsubscribe = useReader.subscribe((state, previous) => {
      if (useAuth.getState().user?.id !== userId) return
      if (state.selectedDocId !== previous.selectedDocId) {
        flush()
        restorePending()
      }
      const current = useReader.getState()
      const docId = current.selectedDocId
      if (docId == null || current.progressRestoredFor !== docId) return
      if (current.currentPage === previous.currentPage && previous.progressRestoredFor === docId) return
      capture(docId, current.currentPage)
    })

    restorePending()
    const initial = useReader.getState()
    const cachedDocument = queryClient.getQueryData<Document>(["document", userId, initial.selectedDocId])
    if (initial.selectedDocId != null && initial.progressRestoredFor === initial.selectedDocId && cachedDocument?.user_id === userId) {
      capture(initial.selectedDocId, initial.currentPage)
    }

    const onHide = () => {
      if (document.visibilityState === "hidden") flush()
    }
    document.addEventListener("visibilitychange", onHide)
    window.addEventListener("pagehide", flush)
    return () => {
      unsubscribe()
      document.removeEventListener("visibilitychange", onHide)
      window.removeEventListener("pagehide", flush)
      flush()
    }
  }, [queryClient, userId])
}
