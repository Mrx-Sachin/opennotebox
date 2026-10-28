"use client"

import { useEffect, useRef, useState } from "react"
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Bookmark as BookmarkIcon,
  Loader2,
  Trash2,
  Pencil,
  Check,
  X,
} from "lucide-react"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import {
  Bookmark,
  createBookmark,
  deleteBookmark,
  getBookmarks,
  updateBookmark,
} from "@/services/api"

function fmt(t: number): string {
  if (!isFinite(t) || t < 0) t = 0
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

function defaultBookmarkName(pageNumber: number): string {
  return `Page ${pageNumber}`
}

function bookmarkDisplayName(bm: Bookmark): string {
  return (bm.label && bm.label.trim()) || defaultBookmarkName(bm.page_number)
}

export default function BottomBar() {
  const { selectedDocId, currentPage, numPages, tts, setPage } = useReader()
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [listOpen, setListOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  // Create-name dialog: open on bookmark click, prefilled with "Page N".
  const [nameDialogOpen, setNameDialogOpen] = useState(false)
  const [nameDraft, setNameDraft] = useState("")
  const [namePage, setNamePage] = useState(1)
  const [namePosition, setNamePosition] = useState(0)
  const [saving, setSaving] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)

  // Inline rename in the list
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState("")
  const [renameSaving, setRenameSaving] = useState(false)
  const renameInputRef = useRef<HTMLInputElement>(null)

  const docId = selectedDocId
  const page = tts.page ?? currentPage
  const busy = tts.status === "preparing"
  const playing = tts.status === "playing"

  useEffect(() => {
    if (docId == null) {
      setBookmarks([])
      return
    }
    getBookmarks(docId).then(setBookmarks).catch(() => setBookmarks([]))
  }, [docId])

  useEffect(() => {
    if (!listOpen && !nameDialogOpen) return
    const close = (e: MouseEvent) => {
      if (!popoverRef.current?.contains(e.target as Node)) {
        setListOpen(false)
        setNameDialogOpen(false)
        setRenamingId(null)
      }
    }
    document.addEventListener("mousedown", close)
    return () => document.removeEventListener("mousedown", close)
  }, [listOpen, nameDialogOpen])

  useEffect(() => {
    if (nameDialogOpen) {
      const t = setTimeout(() => {
        nameInputRef.current?.focus()
        nameInputRef.current?.select()
      }, 0)
      return () => clearTimeout(t)
    }
  }, [nameDialogOpen])

  useEffect(() => {
    if (renamingId != null) {
      const t = setTimeout(() => {
        renameInputRef.current?.focus()
        renameInputRef.current?.select()
      }, 0)
      return () => clearTimeout(t)
    }
  }, [renamingId])

  const activePodcast = useReader((state) => state.activePodcast)
  const activeReading = useReader((state) => state.activeReading)
  if (docId == null || activePodcast != null || (activeReading != null && activeReading.docId !== docId)) return null

  // Podcast/summary playback: the bar becomes a plain audio player with
  // time-based seeking instead of page navigation.
  const directAudio = tts.status !== "idle" && tts.page == null

  const pageFraction = tts.duration > 0 ? tts.currentTime / tts.duration : 0
  const docProgress = directAudio
    ? pageFraction
    : numPages > 0
      ? (page - 1 + pageFraction) / numPages
      : 0

  const handleTrackClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = (e.clientX - rect.left) / rect.width
    if (directAudio) {
      ttsEngine.seekTo(frac * tts.duration)
      return
    }
    if (!numPages) return
    const target = Math.max(1, Math.min(numPages, Math.floor(frac * numPages) + 1))
    setPage(target)
    if (tts.status !== "idle") void ttsEngine.readPage(docId, target, 0)
  }

  const handlePlay = () => {
    if (tts.status === "idle") {
      void ttsEngine.readPage(docId, currentPage, 0)
    } else {
      ttsEngine.toggle()
    }
  }

  /** Open the name prompt; do not create until the user confirms. */
  const handleBookmarkClick = () => {
    setListOpen(false)
    setRenamingId(null)
    setNamePage(page)
    setNamePosition(ttsEngine.currentPosition())
    setNameDraft(defaultBookmarkName(page))
    setNameDialogOpen(true)
  }

  const handleCreateConfirm = async () => {
    if (saving) return
    setSaving(true)
    try {
      const label = nameDraft.trim() || defaultBookmarkName(namePage)
      const bm = await createBookmark(docId, namePage, namePosition, label)
      setBookmarks((prev) => [...prev, bm].sort((a, b) => a.page_number - b.page_number))
      setNameDialogOpen(false)
      setListOpen(true)
    } catch (err) {
      console.error("Failed to create bookmark:", err)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteBookmark = async (id: number) => {
    try {
      await deleteBookmark(id)
      setBookmarks((prev) => prev.filter((b) => b.id !== id))
      if (renamingId === id) setRenamingId(null)
    } catch (err) {
      console.error("Failed to delete bookmark:", err)
    }
  }

  const startRename = (bm: Bookmark) => {
    setRenamingId(bm.id)
    setRenameDraft(bookmarkDisplayName(bm))
  }

  const cancelRename = () => {
    setRenamingId(null)
    setRenameDraft("")
  }

  const confirmRename = async (bm: Bookmark) => {
    if (renameSaving) return
    const label = renameDraft.trim() || defaultBookmarkName(bm.page_number)
    if (label === bookmarkDisplayName(bm)) {
      cancelRename()
      return
    }
    setRenameSaving(true)
    try {
      const updated = await updateBookmark(bm.id, label)
      setBookmarks((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))
      setRenamingId(null)
    } catch (err) {
      console.error("Failed to rename bookmark:", err)
    } finally {
      setRenameSaving(false)
    }
  }

  const jumpToBookmark = (bm: Bookmark) => {
    if (renamingId === bm.id) return
    setListOpen(false)
    setPage(bm.page_number)
    void ttsEngine.readPage(docId, bm.page_number, 0)
  }

  const speedCls =
    tts.speed >= 2 ? "text-[#f5f5db]" : tts.speed !== 1 ? "text-indigo" : "text-cream-dim"

  return (
    <div className="glass relative shrink-0 border-t border-surface">
      {/* 3px progress track with page + bookmark markers */}
      <div
        className="group/track relative h-[3px] w-full cursor-pointer bg-surface"
        onClick={handleTrackClick}
        title={directAudio ? "Seek" : "Jump to page"}
      >
        <div
          className="gradient-primary absolute inset-y-0 left-0 transition-[width] duration-100"
          style={{ width: `${docProgress * 100}%` }}
        />
        {!directAudio &&
          numPages > 1 &&
          numPages <= 60 &&
          Array.from({ length: numPages - 1 }, (_, i) => (
            <div
              key={i}
              className="absolute top-0 h-full w-px bg-void/60"
              style={{ left: `${((i + 1) / numPages) * 100}%` }}
            />
          ))}
        {!directAudio &&
          bookmarks.map((bm) => (
            <div
              key={bm.id}
              className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rust shadow-[0_0_6px_rgba(193,68,14,0.8)]"
              style={{
                left: `${(((bm.page_number - 1 + (bm.position ?? 0)) / Math.max(numPages, 1)) * 100).toFixed(2)}%`,
              }}
              title={bookmarkDisplayName(bm)}
            />
          ))}
      </div>

      <div className="grid h-8 grid-cols-3 items-center px-3 text-xs">
        <div className="flex min-w-0 items-center gap-3 text-cream-dim">
          <span className="hidden whitespace-nowrap tabular-nums sm:inline">
            {fmt(tts.currentTime)} / {fmt(tts.duration)}
          </span>
          <span className="truncate whitespace-nowrap tabular-nums">
            {directAudio ? "Audio" : `${page}${numPages ? `/${numPages}` : ""}`}
          </span>
        </div>

        <div className="flex items-center justify-center gap-1">
          <button
            onClick={() => (directAudio ? ttsEngine.skipSeconds(-15) : ttsEngine.skipPage(-1))}
            disabled={!directAudio && page <= 1}
            title={directAudio ? "Back 15s (←)" : "Previous page (←)"}
            className="rounded p-1 text-cream-dim transition-colors hover:text-cream disabled:opacity-30"
          >
            <SkipBack className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handlePlay}
            disabled={busy}
            title={playing ? "Pause (Space)" : "Play (Space)"}
            className="gradient-primary mx-1 flex h-6 w-6 items-center justify-center rounded-full text-white shadow-glow transition-transform hover:scale-105 disabled:opacity-60"
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : playing ? (
              <Pause className="h-3 w-3" />
            ) : (
              <Play className="ml-px h-3 w-3" />
            )}
          </button>
          <button
            onClick={() => (directAudio ? ttsEngine.skipSeconds(15) : ttsEngine.skipPage(1))}
            disabled={!directAudio && numPages > 0 && page >= numPages}
            title={directAudio ? "Forward 15s (→)" : "Next page (→)"}
            className="rounded p-1 text-cream-dim transition-colors hover:text-cream disabled:opacity-30"
          >
            <SkipForward className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex items-center justify-end gap-1">
          <button
            onClick={() => ttsEngine.setSpeed(Math.max(0.5, tts.speed - 0.25))}
            title="Slower"
            className="rounded px-1 py-0.5 text-cream-dim transition-colors hover:text-cream"
          >
            −
          </button>
          <span className={`w-8 text-center tabular-nums ${speedCls}`}>{tts.speed}x</span>
          <button
            onClick={() => ttsEngine.setSpeed(Math.min(3, tts.speed + 0.25))}
            title="Faster"
            className="rounded px-1 py-0.5 text-cream-dim transition-colors hover:text-cream"
          >
            +
          </button>
          <button
            onClick={() => ttsEngine.toggleMute()}
            title="Mute (Ctrl+M)"
            className="ml-1 rounded p-1 text-cream-dim transition-colors hover:text-cream"
          >
            {tts.muted ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5" />}
          </button>
          <div className="relative" ref={popoverRef}>
            <button
              onClick={handleBookmarkClick}
              onContextMenu={(e) => {
                e.preventDefault()
                setNameDialogOpen(false)
                setListOpen((v) => !v)
              }}
              onDoubleClick={() => {
                setNameDialogOpen(false)
                setListOpen((v) => !v)
              }}
              title="Bookmark this spot (double-click / right-click to list)"
              className="rounded p-1 text-cream-dim transition-colors hover:text-rust"
            >
              <BookmarkIcon className="h-3.5 w-3.5" />
            </button>

            {/* Name prompt when creating */}
            {nameDialogOpen && (
              <div className="glass absolute bottom-9 right-0 z-50 w-64 rounded-lg border border-surface p-3 shadow-glow">
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-cream-dim">
                  New bookmark
                </p>
                <label className="mb-1 block text-[11px] text-cream-dim">Name</label>
                <input
                  ref={nameInputRef}
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault()
                      void handleCreateConfirm()
                    } else if (e.key === "Escape") {
                      e.preventDefault()
                      setNameDialogOpen(false)
                    }
                  }}
                  placeholder={defaultBookmarkName(namePage)}
                  className="mb-2 w-full rounded-md border border-surface bg-void px-2 py-1.5 text-xs text-cream placeholder:text-cream-dim/50 focus:border-indigo/50 focus:outline-none"
                />
                <p className="mb-2 text-[10px] text-cream-dim">
                  Page {namePage}
                  {namePosition > 0 ? ` · ${Math.round(namePosition * 100)}%` : ""}
                </p>
                <div className="flex items-center justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => setNameDialogOpen(false)}
                    className="rounded-md px-2.5 py-1 text-[11px] text-cream-dim hover:bg-surface hover:text-cream"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleCreateConfirm()}
                    disabled={saving}
                    className="gradient-primary flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-50"
                  >
                    {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                    Save
                  </button>
                </div>
              </div>
            )}

            {/* Bookmark list with rename */}
            {listOpen && !nameDialogOpen && (
              <div className="glass absolute bottom-9 right-0 z-50 w-72 rounded-lg border border-surface p-2 shadow-glow">
                <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-cream-dim">
                  Bookmarks
                </p>
                {bookmarks.length === 0 ? (
                  <p className="px-2 py-3 text-center text-xs text-cream-dim">No bookmarks yet</p>
                ) : (
                  <ul className="reader-scroll max-h-48 space-y-0.5 overflow-y-auto">
                    {bookmarks.map((bm) => (
                      <li key={bm.id} className="group flex items-center gap-0.5">
                        {renamingId === bm.id ? (
                          <>
                            <input
                              ref={renameInputRef}
                              value={renameDraft}
                              onChange={(e) => setRenameDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault()
                                  void confirmRename(bm)
                                } else if (e.key === "Escape") {
                                  e.preventDefault()
                                  cancelRename()
                                }
                              }}
                              className="min-w-0 flex-1 rounded border border-indigo/40 bg-void px-2 py-1 text-xs text-cream focus:outline-none"
                            />
                            <button
                              type="button"
                              onClick={() => void confirmRename(bm)}
                              disabled={renameSaving}
                              title="Save name"
                              className="rounded p-1 text-indigo hover:bg-indigo/15"
                            >
                              {renameSaving ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <Check className="h-3 w-3" />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={cancelRename}
                              title="Cancel"
                              className="rounded p-1 text-cream-dim hover:bg-surface hover:text-cream"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => jumpToBookmark(bm)}
                              className="min-w-0 flex-1 rounded px-2 py-1.5 text-left text-xs text-cream transition-colors hover:bg-surface"
                            >
                              <span className="truncate block">{bookmarkDisplayName(bm)}</span>
                              <span className="text-[10px] text-cream-dim">
                                p.{bm.page_number}
                                {bm.position != null && bm.position > 0
                                  ? ` · ${Math.round(bm.position * 100)}%`
                                  : ""}
                              </span>
                            </button>
                            <button
                              onClick={() => startRename(bm)}
                              title="Rename"
                              className="hidden shrink-0 rounded p-1 text-cream-dim hover:text-indigo group-hover:block"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                            <button
                              onClick={() => handleDeleteBookmark(bm.id)}
                              title="Delete bookmark"
                              className="hidden shrink-0 rounded p-1 text-cream-dim hover:text-[#ef4444] group-hover:block"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
