"use client"

/**
 * Multi-file upload queue.
 *
 * This is deliberately NOT React state owned by UploadModal. It is a
 * plain module-level singleton with a subscribe/getSnapshot pair (fed to
 * React via useSyncExternalStore) so that uploads keep running - and their
 * per-file progress keeps updating - even if the user closes the modal.
 * Re-opening the modal just re-subscribes to whatever is already in
 * flight. This is the "don't block the UI" requirement: closing the
 * modal is a view change, not a cancel.
 *
 * api.ts already exports a configured axios instance (default export) with
 * the auth-token interceptor wired up, so we import and reuse that instead
 * of standing up a second one - the interceptor logic lives in exactly one
 * place.
 */
import axios from "axios"
import api, { Document, LayoutMode, getDocumentStatus } from "@/services/api"
import { uid } from "@/lib/uid"

// Mirrors backend/app/routers/documents.py ALLOWED_UPLOAD_EXTS. The
// backend is the source of truth and re-validates regardless; this is a
// client-side pre-check so a user doesn't wait through an upload just to
// be told the format is unsupported.
export const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".txt", ".md", ".epub"]
export const ALLOWED_FORMATS_LABEL = "PDF, EPUB, DOCX, PPTX, TXT, MD"

// Mirrors backend Settings.upload_max_size_mb default (backend/app/config.py).
// The backend enforces the real limit server-side; this just avoids
// spending a full upload's worth of bandwidth on a file that will
// definitely be rejected.
export const UPLOAD_MAX_SIZE_MB = 200

// Browsers cap concurrent connections per origin (~6), and every parse is
// its own Celery task competing for worker capacity, plus the dashboard's
// own 5s polling and per-file status polling below all share the origin.
// 3 concurrent uploads keeps several files moving at once without
// starving everything else sharing the connection pool.
const MAX_CONCURRENT_UPLOADS = 3

// How long to keep polling a single document's processing status before
// giving up and just leaving it as "processing" - the dashboard's own
// document list polling will pick up the eventual result regardless, this
// is only for the in-modal per-file status.
const POLL_TIMEOUT_MS = 10 * 60 * 1000
const POLL_INTERVAL_MS = 2000

const TERMINAL_DONE_STATUSES = new Set(["extracted", "summarizing_pages", "completed"])

export type UploadStatus = "queued" | "uploading" | "processing" | "done" | "error" | "canceled"

export interface UploadItem {
  id: string
  file: File
  layoutMode: LayoutMode
  status: UploadStatus
  percent: number
  error?: string
  docId?: number
}

function validateFile(file: File): string | null {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase()
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported format ${ext || "(none)"}`
  }
  if (file.size > UPLOAD_MAX_SIZE_MB * 1024 * 1024) {
    return `File too large (max ${UPLOAD_MAX_SIZE_MB}MB)`
  }
  return null
}

/** Upload a single file with progress + abort support. Not exported from
    api.ts (that uploadFile() has no AbortSignal), so it lives here. */
function postUpload(
  file: File,
  layoutMode: LayoutMode,
  onProgress: (percent: number) => void,
  signal: AbortSignal
) {
  const formData = new FormData()
  formData.append("file", file)
  formData.append("layout_mode", layoutMode)
  return api
    .post<Document>("/api/documents/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      signal,
      onUploadProgress: (e) => {
        if (e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    })
    .then((r) => r.data)
}

class UploadQueueStore {
  private items: UploadItem[] = []
  private controllers = new Map<string, AbortController>()
  private activeUploads = 0
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = () => this.items

  private emit() {
    // New array identity so useSyncExternalStore's snapshot comparison
    // (Object.is) sees a change on every mutation.
    this.items = [...this.items]
    this.listeners.forEach((l) => l())
  }

  private patch(id: string, patch: Partial<UploadItem>) {
    this.items = this.items.map((it) => (it.id === id ? { ...it, ...patch } : it))
    this.emit()
  }

  /** Queue a batch of files at a shared default layout mode. Files that
      fail client-side validation are still added, already marked "error",
      so the user sees exactly why each rejected file didn't go through
      instead of it silently vanishing. */
  add(files: File[], layoutMode: LayoutMode): string[] {
    const newItems: UploadItem[] = files.map((file) => {
      const validationError = validateFile(file)
      return {
        id: uid(),
        file,
        layoutMode,
        status: validationError ? "error" : "queued",
        percent: 0,
        error: validationError ?? undefined,
      }
    })
    this.items = [...this.items, ...newItems]
    this.emit()
    this.pump()
    return newItems.map((i) => i.id)
  }

  /** Change the layout mode for one queued (not yet uploading) file. */
  setLayoutMode(id: string, layoutMode: LayoutMode) {
    const item = this.items.find((i) => i.id === id)
    if (item && item.status === "queued") this.patch(id, { layoutMode })
  }

  retry(id: string) {
    const item = this.items.find((i) => i.id === id)
    if (!item) return
    const validationError = validateFile(item.file)
    this.patch(id, {
      status: validationError ? "error" : "queued",
      error: validationError ?? undefined,
      percent: 0,
    })
    this.pump()
  }

  /** Cancel an in-flight upload, or drop a queued/finished entry entirely. */
  remove(id: string) {
    this.controllers.get(id)?.abort()
    this.controllers.delete(id)
    this.items = this.items.filter((i) => i.id !== id)
    this.emit()
  }

  /** Drop everything that isn't actively uploading/processing - used when
      the user clears a completed batch from view without touching
      in-flight work. */
  clearFinished() {
    for (const it of this.items) {
      if (it.status !== "uploading" && it.status !== "processing") {
        this.controllers.get(it.id)?.abort()
        this.controllers.delete(it.id)
      }
    }
    this.items = this.items.filter((i) => i.status === "uploading" || i.status === "processing")
    this.emit()
  }

  private pump() {
    while (this.activeUploads < MAX_CONCURRENT_UPLOADS) {
      const next = this.items.find((i) => i.status === "queued")
      if (!next) break
      this.startUpload(next.id)
    }
  }

  private startUpload(id: string) {
    const item = this.items.find((i) => i.id === id)
    if (!item) return
    this.activeUploads++
    const controller = new AbortController()
    this.controllers.set(id, controller)
    this.patch(id, { status: "uploading", percent: 0, error: undefined })

    postUpload(
      item.file,
      item.layoutMode,
      (percent) => this.patch(id, { percent }),
      controller.signal
    )
      .then((doc) => {
        this.patch(id, { status: "processing", percent: 100, docId: doc.id })
        this.pollProcessing(id, doc.id)
      })
      .catch((err) => {
        if (axios.isAxiosError(err) && err.code === "ERR_CANCELED") {
          this.patch(id, { status: "canceled" })
        } else {
          const message =
            (axios.isAxiosError(err) && (err.response?.data?.detail as string)) ||
            "Upload failed"
          this.patch(id, { status: "error", error: message })
        }
      })
      .finally(() => {
        this.controllers.delete(id)
        this.activeUploads--
        this.pump()
      })
  }

  /** Poll a just-uploaded document until extraction finishes (or fails),
      so the queue can show "processing" -> "done" instead of freezing at
      100% the moment the HTTP upload itself completes - the parse still
      runs as a background Celery task after that point. */
  private pollProcessing(id: string, docId: number, startedAt = Date.now()) {
    getDocumentStatus(docId)
      .then((status: { processing_status: string }) => {
        // The item may have been removed from the queue while this
        // request was in flight - stop polling instead of resurrecting it.
        if (!this.items.some((i) => i.id === id)) return

        if (status.processing_status === "failed") {
          this.patch(id, { status: "error", error: "Processing failed" })
          return
        }
        if (TERMINAL_DONE_STATUSES.has(status.processing_status)) {
          this.patch(id, { status: "done" })
          return
        }
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          // Give up polling in the modal; the doc keeps processing
          // server-side and will show up correctly in the main list.
          this.patch(id, { status: "done" })
          return
        }
        setTimeout(() => this.pollProcessing(id, docId, startedAt), POLL_INTERVAL_MS)
      })
      .catch(() => {
        // Transient network hiccup while polling - don't fail the whole
        // upload over it, just stop watching.
      })
  }
}

export const uploadQueue = new UploadQueueStore()
