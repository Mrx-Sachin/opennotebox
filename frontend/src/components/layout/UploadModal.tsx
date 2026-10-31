"use client"

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"
import {
  Upload,
  Link as LinkIcon,
  X,
  CheckCircle2,
  AlertCircle,
  Loader2,
  RotateCcw,
  FileClock,
} from "lucide-react"
import { uploadFromUrl, LayoutMode } from "@/services/api"
import {
  uploadQueue,
  UploadItem,
  ALLOWED_EXTENSIONS,
  ALLOWED_FORMATS_LABEL,
  UPLOAD_MAX_SIZE_MB,
} from "@/services/uploadApi"
import { useReader } from "@/stores/reader"
import Link from "next/link"

interface UploadModalProps {
  onUploaded: () => void
  notebookId?: number | null
  notebookName?: string
}

const LAYOUT_OPTIONS: { value: LayoutMode; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Auto",
    hint: "Detect columns when the page looks two-column",
  },
  {
    value: "research_paper",
    label: "Research paper",
    hint: "Two-column papers (Whisper, BERT, arXiv)",
  },
  {
    value: "single_column",
    label: "Single column",
    hint: "Books, reports, one-column PDFs",
  },
]

const STATUS_LABEL: Record<UploadItem["status"], string> = {
  queued: "Queued",
  uploading: "Uploading",
  processing: "Processing",
  done: "Done",
  error: "Failed",
  canceled: "Canceled",
}

export default function UploadModal({ onUploaded, notebookId, notebookName }: UploadModalProps) {
  const { uploadModalOpen, setUploadModalOpen } = useReader()
  const [tab, setTab] = useState<"file" | "url">("file")
  const [url, setUrl] = useState("")
  const [urlBusy, setUrlBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("auto")
  const inputRef = useRef<HTMLInputElement>(null)

  // The queue is a module-level singleton (see services/uploadApi.ts) so
  // uploads survive the modal closing. useSyncExternalStore subscribes
  // this component to it without owning the state itself.
  const entries = useSyncExternalStore(uploadQueue.subscribe, uploadQueue.getSnapshot, uploadQueue.getSnapshot)

  // onUploaded (refreshes the sidebar doc list) needs to fire once per
  // document as soon as its row exists server-side, not once per batch -
  // otherwise a document uploaded early in a long batch wouldn't appear
  // until everything finished. Track which docIds we've already notified
  // for so re-renders don't re-fire it.
  const notifiedRef = useRef(new Set<string>())
  useEffect(() => {
    for (const e of entries) {
      if (e.docId != null && !notifiedRef.current.has(e.id)) {
        notifiedRef.current.add(e.id)
        onUploaded()
      }
    }
  }, [entries, onUploaded])

  const startUploads = useCallback(
    (files: File[]) => {
      if (files.length === 0) return
      uploadQueue.add(files, layoutMode, notebookId)
    },
    [layoutMode, notebookId]
  )

  const handleUrl = async () => {
    if (!url.trim()) return
    setUrlBusy(true)
    try {
      await uploadFromUrl(url.trim(), notebookId)
      setUrl("")
      onUploaded()
      setUploadModalOpen(false)
    } catch (err) {
      console.error("URL upload failed:", err)
    } finally {
      setUrlBusy(false)
    }
  }

  if (!uploadModalOpen) return null

  const anyActive = entries.some((e) => e.status === "uploading" || e.status === "processing" || e.status === "queued")

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={() => setUploadModalOpen(false)}
    >
      <div
        className="glass w-full max-w-lg rounded-2xl border border-surface p-6 shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-lg font-bold text-cream">Add sources</h2>
          <button
            onClick={() => setUploadModalOpen(false)}
            className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
            title={anyActive ? "Close - uploads keep running in the background" : "Close"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {notebookName && <p className="mb-4 text-xs text-cream-dim">Adding to {notebookName}</p>}

        <div className="mb-4 flex gap-1 rounded-lg bg-void p-1">
          {(["file", "url"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === t ? "gradient-primary text-white" : "text-cream-dim hover:text-cream"
              }`}
            >
              {t === "file" ? "Upload files" : "From URL"}
            </button>
          ))}
        </div>

        <div className="mb-4">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
            PDF layout {tab === "file" ? "(default for new files)" : ""}
          </p>
          <div className="flex gap-1 rounded-lg bg-void p-1">
            {LAYOUT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title={opt.hint}
                onClick={() => setLayoutMode(opt.value)}
                className={`flex-1 rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors ${
                  layoutMode === opt.value
                    ? "gradient-primary text-white"
                    : "text-cream-dim hover:text-cream"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[10px] text-cream-dim">
            {LAYOUT_OPTIONS.find((o) => o.value === layoutMode)?.hint}
          </p>
        </div>

        {tab === "file" ? (
          <>
            <div
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                startUploads(Array.from(e.dataTransfer.files))
              }}
              className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-10 transition-colors ${
                dragOver
                  ? "border-indigo bg-indigo/10"
                  : "border-surface hover:border-indigo/50 hover:bg-surface/40"
              }`}
            >
              <Upload className="h-6 w-6 text-indigo" />
              <p className="text-sm text-cream">Drop files here or click to browse</p>
              <p className="text-xs text-cream-dim">
                {ALLOWED_FORMATS_LABEL} · multiple allowed · max {UPLOAD_MAX_SIZE_MB}MB each
              </p>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={ALLOWED_EXTENSIONS.join(",")}
                className="hidden"
                onChange={(e) => {
                  startUploads(Array.from(e.target.files ?? []))
                  e.target.value = ""
                }}
              />
            </div>

            {entries.length > 0 && (
              <ul className="reader-scroll mt-4 max-h-60 space-y-2 overflow-y-auto">
                {entries.map((entry) => (
                  <li key={entry.id} className="rounded-lg bg-void px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-xs text-cream" title={entry.file.name}>
                        {entry.file.name}
                      </span>

                      {entry.docId != null && (
                        <Link
                          href={`/dashboard/read/${entry.docId}`}
                          onClick={() => setUploadModalOpen(false)}
                          className="shrink-0 rounded px-2 py-1 text-xs text-indigo hover:bg-surface"
                        >
                          Open document
                        </Link>
                      )}

                      {entry.status === "queued" && (
                        <FileClock className="h-3.5 w-3.5 shrink-0 text-cream-dim" />
                      )}
                      {entry.status === "uploading" && (
                        <span className="shrink-0 text-[10px] tabular-nums text-cream-dim">
                          {entry.percent}%
                        </span>
                      )}
                      {entry.status === "processing" && (
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-indigo" />
                      )}
                      {entry.status === "done" && (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-[#4ade80]" />
                      )}
                      {(entry.status === "error" || entry.status === "canceled") && (
                        <AlertCircle className="h-3.5 w-3.5 shrink-0 text-[#ef4444]" />
                      )}

                      {entry.status === "error" && (
                        <button
                          onClick={() => uploadQueue.retry(entry.id)}
                          title="Retry this file"
                          className="shrink-0 rounded p-0.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}

                      <button
                        onClick={() => uploadQueue.remove(entry.id)}
                        title={entry.status === "uploading" ? "Cancel upload" : "Remove"}
                        className="shrink-0 rounded p-0.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    <div className="mt-1 flex items-center justify-between gap-2">
                      <span className="text-[10px] text-cream-dim">
                        {STATUS_LABEL[entry.status]}
                        {entry.error ? ` - ${entry.error}` : ""}
                      </span>

                      {/* Per-file layout override, only meaningful for PDFs and
                          only while the file hasn't started uploading yet - the
                          batch-wide picker above sets the default at add time. */}
                      {entry.status === "queued" && entry.file.name.toLowerCase().endsWith(".pdf") && (
                        <select
                          value={entry.layoutMode}
                          onChange={(e) => uploadQueue.setLayoutMode(entry.id, e.target.value as LayoutMode)}
                          className="rounded border border-surface bg-void px-1 py-0.5 text-[10px] text-cream-dim focus:outline-none"
                        >
                          {LAYOUT_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>

                    {entry.status === "uploading" && (
                      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface">
                        <div
                          className="gradient-primary h-full transition-[width] duration-150"
                          style={{ width: `${entry.percent}%` }}
                        />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {entries.length > 0 && (
              <button
                onClick={() => {
                  // Nothing left running - clear the list so the next visit
                  // to this modal starts clean instead of showing a pile of
                  // old "Done" rows. If something is still uploading or
                  // processing, leave the queue alone; it keeps running
                  // and the user can check back on it later.
                  if (!anyActive) uploadQueue.clearFinished()
                  setUploadModalOpen(false)
                }}
                className="gradient-primary mt-4 w-full rounded-lg py-2 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.01]"
              >
                {anyActive ? "Close - keep uploading in background" : "Done"}
              </button>
            )}
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-lg border border-surface bg-void px-3 py-2">
              <LinkIcon className="h-4 w-4 shrink-0 text-cream-dim" />
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleUrl()}
                placeholder="https://example.com/paper.pdf"
                className="w-full bg-transparent text-sm text-cream placeholder:text-cream-dim focus:outline-none"
              />
            </div>
            <button
              onClick={handleUrl}
              disabled={urlBusy || !url.trim()}
              className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:opacity-50"
            >
              {urlBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Fetch document
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
