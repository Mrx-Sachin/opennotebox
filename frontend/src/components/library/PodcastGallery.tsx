"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import * as Dialog from "@radix-ui/react-dialog"
import {
  AlertCircle,
  BookOpen,
  Headphones,
  Loader2,
  Maximize2,
  Pause,
  Play,
  Trash2,
  X,
} from "lucide-react"
import {
  Podcast,
  getPodcasts,
  deletePodcast,
} from "@/services/api"
import { useAuth } from "@/stores/auth"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"

function formatTime(seconds?: number | null): string {
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return "0:00"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export default function PodcastGallery() {
  const queryClient = useQueryClient()
  const user = useAuth((state) => state.user)
  const tts = useReader((state) => state.tts)
  const globalActivePodcast = useReader((state) => state.activePodcast)
  const setGlobalExpandedPodcast = useReader((state) => state.setExpandedPodcast)
  const activePodcastId = globalActivePodcast?.id ?? null
  const [loadingId, setLoadingId] = useState<number | null>(null)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const [podcastToDelete, setPodcastToDelete] = useState<Podcast | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const playbackTokenRef = useRef(0)
  const isMountedRef = useRef(true)
  const loadingIdRef = useRef<number | null>(null)

  const { data: podcasts, isPending, isError, refetch } = useQuery({
    queryKey: ["podcasts", "all", user?.id],
    queryFn: () => getPodcasts(),
    enabled: user != null,
    refetchInterval: (query) => {
      const items = query.state.data
      return items && items.some((item) => item.status === "generating") ? 3000 : false
    },
  })

  useEffect(() => {
    const playbackToken = playbackTokenRef
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      playbackToken.current++
    }
  }, [])

  useEffect(() => {
    loadingIdRef.current = loadingId
  }, [loadingId])

  const handleConfirmDelete = async () => {
    if (!podcastToDelete) return
    const targetPodcast = podcastToDelete
    const targetUserId = user?.id
    const targetDocumentId = targetPodcast.document_id
    setIsDeleting(true)
    setDeleteError(null)

    try {
      await deletePodcast(targetPodcast.id)

      if (targetUserId != null) {
        queryClient.setQueryData<Podcast[]>(
          ["podcasts", "all", targetUserId],
          (previousPodcasts) => {
            if (!previousPodcasts) return []
            return previousPodcasts.filter((item) => item.id !== targetPodcast.id)
          }
        )
      }

      const currentAuthenticatedUser = useAuth.getState().user
      const isMatchingAccount =
        currentAuthenticatedUser?.id != null &&
        currentAuthenticatedUser.id === targetUserId

      if (isMatchingAccount) {
        const currentActivePodcast = useReader.getState().activePodcast
        if (currentActivePodcast?.id === targetPodcast.id) {
          playbackTokenRef.current++
          ttsEngine.stop()
        }
        const currentExpandedPodcast = useReader.getState().expandedPodcast
        if (currentExpandedPodcast?.id === targetPodcast.id) {
          useReader.getState().setExpandedPodcast(null)
        }
      }

      if (!isMatchingAccount || !isMountedRef.current) {
        return
      }

      if (loadingIdRef.current === targetPodcast.id) {
        setLoadingId(null)
      }

      if (targetDocumentId != null) {
        queryClient.setQueryData<Podcast[]>(
          ["podcasts", targetDocumentId],
          (previousPodcasts) => {
            if (!previousPodcasts) return []
            return previousPodcasts.filter((item) => item.id !== targetPodcast.id)
          }
        )
        queryClient.setQueryData<Podcast[]>(
          ["podcasts", "document", targetDocumentId],
          (previousPodcasts) => {
            if (!previousPodcasts) return []
            return previousPodcasts.filter((item) => item.id !== targetPodcast.id)
          }
        )
      }

      void queryClient.invalidateQueries({
        queryKey: ["podcasts"],
      })

      setPodcastToDelete(null)
    } catch {
      const currentAuthenticatedUser = useAuth.getState().user
      const isMatchingAccount =
        currentAuthenticatedUser?.id != null &&
        currentAuthenticatedUser.id === targetUserId
      if (isMountedRef.current && isMatchingAccount) {
        setDeleteError("Failed to delete podcast. Please try again.")
      }
    } finally {
      const currentAuthenticatedUser = useAuth.getState().user
      const isMatchingAccount =
        currentAuthenticatedUser?.id != null &&
        currentAuthenticatedUser.id === targetUserId
      if (isMountedRef.current && isMatchingAccount) {
        setIsDeleting(false)
      }
    }
  }

  const handlePlayToggle = async (podcast: Podcast) => {
    if (activePodcastId === podcast.id) {
      ttsEngine.toggle()
      return
    }

    playbackTokenRef.current++
    const currentToken = playbackTokenRef.current
    setLoadingId(podcast.id)
    setPlaybackError(null)

    try {
      await ttsEngine.playPodcast(podcast)
      if (!isMountedRef.current || playbackTokenRef.current !== currentToken) {
        return
      }
      const latestState = useReader.getState().tts
      if (latestState.status === "idle") {
        setPlaybackError(
          `Unable to play podcast "${podcast.transcript?.title || podcast.document_title || "Audio"}". Please try again.`
        )
      }
    } catch {
      if (isMountedRef.current && playbackTokenRef.current === currentToken) {
        setPlaybackError(
          `Unable to play podcast "${podcast.transcript?.title || podcast.document_title || "Audio"}". Please try again.`
        )
      }
    } finally {
      if (isMountedRef.current && playbackTokenRef.current === currentToken) {
        setLoadingId(null)
      }
    }
  }


  return (
    <section
      aria-labelledby="podcast-gallery-heading"
      className="podcast-gallery-section"
      data-testid="podcast-gallery-section"
    >
      <div className="library-section-heading">
        <h2 id="podcast-gallery-heading">Podcasts</h2>
        <span className="library-result-count" aria-live="polite">
          {podcasts ? `${podcasts.length} ${podcasts.length === 1 ? "podcast" : "podcasts"}` : "Loading podcasts"}
        </span>
      </div>

      {playbackError && (
        <div className="podcast-playback-error" role="alert">
          <AlertCircle size={16} />
          <span>{playbackError}</span>
          <button
            type="button"
            onClick={() => setPlaybackError(null)}
            aria-label="Dismiss playback error"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {isPending ? (
        <div className="library-shelf-loading" aria-busy="true">
          <Loader2 size={24} className="animate-spin" />
          <p>Loading podcasts...</p>
        </div>
      ) : isError ? (
        <div className="podcast-empty-state notebook-load-error" role="alert">
          <h3>Podcasts could not be loaded</h3>
          <p>There was a problem fetching your audio discussions.</p>
          <button type="button" onClick={() => void refetch()}>
            Try again
          </button>
        </div>
      ) : podcasts.length === 0 ? (
        <div className="podcast-empty-state" data-testid="podcast-empty-state">
          <Headphones size={36} strokeWidth={1.4} />
          <h3>No podcasts generated yet</h3>
          <p>
            Generate conversational audio discussions from your books and notes inside any document reader.
          </p>
        </div>
      ) : (
        <div className="podcast-grid">
          {podcasts.map((podcast) => {
            const isActive = activePodcastId === podcast.id
            const isPlaying = isActive && tts.status === "playing"
            const isBuffering = isActive && (tts.status === "preparing" || loadingId === podcast.id)
            const displayTitle =
              podcast.transcript?.title ||
              (podcast.scope === "section" && podcast.section_title
                ? podcast.section_title
                : podcast.document_title) ||
              `Podcast ${podcast.id}`
            const bookTitle = podcast.document_title || `Document ${podcast.document_id}`

            return (
              <article
                key={podcast.id}
                data-testid={`podcast-card-${podcast.id}`}
                className={`podcast-card ${isActive ? "is-active" : ""}`}
                aria-label={`Podcast: ${displayTitle}`}
              >
                <div className="podcast-card-header">
                  <div className="podcast-card-icon" aria-hidden="true">
                    <Headphones size={18} />
                  </div>
                  <div className="podcast-card-title-wrap">
                    <h3 className="podcast-card-title" title={displayTitle}>
                      {displayTitle}
                    </h3>
                    <Link
                      href={`/dashboard/read/${podcast.document_id}`}
                      className="podcast-source-link"
                      aria-label={`Open source book ${bookTitle}`}
                      data-testid={`podcast-book-link-${podcast.id}`}
                    >
                      <BookOpen size={13} aria-hidden="true" />
                      <span>{bookTitle}</span>
                    </Link>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                    {podcast.status === "completed" && (
                      <button
                        type="button"
                        data-testid={`podcast-expand-${podcast.id}`}
                        className="podcast-card-action-btn"
                        onClick={() => {
                          setGlobalExpandedPodcast(podcast)
                          if (activePodcastId !== podcast.id) {
                            void handlePlayToggle(podcast)
                          }
                        }}
                        aria-label={`Open player for "${displayTitle}"`}
                        title="Open full player & transcript"
                      >
                        <Maximize2 size={15} />
                      </button>
                    )}
                    <button
                      type="button"
                      data-testid={`podcast-delete-${podcast.id}`}
                      className="podcast-card-action-btn"
                      onClick={() => {
                        setDeleteError(null)
                        setPodcastToDelete(podcast)
                      }}
                      aria-label={`Delete podcast "${displayTitle}"`}
                      title={`Delete podcast "${displayTitle}"`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>

                <div className="podcast-meta-row">
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    {podcast.status === "generating" ? (
                      <span className="podcast-badge podcast-badge-generating">
                        <Loader2 size={11} className="animate-spin" />
                        Generating
                      </span>
                    ) : podcast.status === "failed" ? (
                      <span className="podcast-badge podcast-badge-failed">
                        <AlertCircle size={11} />
                        Failed
                      </span>
                    ) : (
                      <>
                        <span className="podcast-badge">
                          {podcast.scope === "section" ? "Chapter" : "Full book"}
                        </span>
                        <span className="podcast-time-label">
                          {formatTime(podcast.duration)}
                        </span>
                      </>
                    )}
                  </div>

                  {podcast.status === "completed" && (
                    <button
                      type="button"
                      data-testid={`podcast-play-${podcast.id}`}
                      className="podcast-play-btn"
                      disabled={isBuffering}
                      onClick={() => void handlePlayToggle(podcast)}
                      aria-label={isPlaying ? `Pause ${displayTitle}` : `Play ${displayTitle}`}
                      title={isPlaying ? "Pause" : "Play"}
                    >
                      {isBuffering ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : isPlaying ? (
                        <Pause size={14} />
                      ) : (
                        <Play size={14} style={{ marginLeft: "2px" }} />
                      )}
                    </button>
                  )}
                </div>
              </article>
            )
          })}
        </div>
      )}

      <Dialog.Root
        open={Boolean(podcastToDelete)}
        onOpenChange={(dialogOpenState) => {
          if (!dialogOpenState && !isDeleting) {
            setPodcastToDelete(null)
            setDeleteError(null)
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="notebook-dialog-overlay" />
          <Dialog.Content
            className="notebook-dialog podcast-delete-dialog"
            data-testid="podcast-delete-dialog"
            aria-describedby="podcast-delete-description"
          >
            <div className="notebook-dialog-heading">
              <Dialog.Title>Delete podcast</Dialog.Title>
              <Dialog.Close
                disabled={isDeleting}
                aria-label="Close delete dialog"
                onClick={() => {
                  setPodcastToDelete(null)
                  setDeleteError(null)
                }}
              >
                <X size={19} />
              </Dialog.Close>
            </div>
            <p id="podcast-delete-description">
              Are you sure you want to delete &ldquo;
              {podcastToDelete?.transcript?.title ||
                (podcastToDelete?.scope === "section" && podcastToDelete?.section_title
                  ? podcastToDelete.section_title
                  : podcastToDelete?.document_title) ||
                `Podcast ${podcastToDelete?.id}`}
              &rdquo;? The audio file will be permanently removed.
            </p>
            {deleteError && (
              <div
                className="podcast-playback-error"
                role="alert"
                style={{ marginTop: "14px", marginBottom: "0px" }}
              >
                <AlertCircle size={16} />
                <span>{deleteError}</span>
              </div>
            )}
            <div className="notebook-dialog-actions">
              <button
                type="button"
                className="library-secondary-button"
                disabled={isDeleting}
                onClick={() => {
                  setPodcastToDelete(null)
                  setDeleteError(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="confirm-delete-podcast-button"
                className="library-delete-confirm-button"
                disabled={isDeleting}
                onClick={() => void handleConfirmDelete()}
              >
                {isDeleting && <Loader2 size={14} className="animate-spin" />}
                <span>{isDeleting ? "Deleting..." : "Delete podcast"}</span>
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  )
}
