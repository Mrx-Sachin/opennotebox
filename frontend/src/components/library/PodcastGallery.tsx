"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import {
  AlertCircle,
  BookOpen,
  Headphones,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import {
  Podcast,
  getPodcasts,
  getPodcastAudioUrl,
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
  const user = useAuth((state) => state.user)
  const tts = useReader((state) => state.tts)
  const [activePodcastId, setActivePodcastId] = useState<number | null>(null)
  const [loadingId, setLoadingId] = useState<number | null>(null)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const playbackTokenRef = useRef(0)

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
    return () => {
      playbackToken.current++
      ttsEngine.stop()
    }
  }, [])

  useEffect(() => {
    if (tts.status === "idle" || tts.page != null) {
      setActivePodcastId(null)
    }
  }, [tts.status, tts.page])

  const activePodcast = podcasts?.find((item) => item.id === activePodcastId)

  const handlePlayToggle = async (podcast: Podcast) => {
    setPlaybackError(null)
    if (activePodcastId === podcast.id) {
      ttsEngine.toggle()
      return
    }

    const currentToken = ++playbackTokenRef.current
    setActivePodcastId(podcast.id)
    setLoadingId(podcast.id)

    try {
      await ttsEngine.playAudioUrl(getPodcastAudioUrl(podcast.id))
      if (playbackTokenRef.current !== currentToken) {
        return
      }
      const latestState = useReader.getState().tts
      if (latestState.status === "idle") {
        setActivePodcastId(null)
        setPlaybackError(
          `Unable to play podcast "${podcast.transcript?.title || podcast.document_title || "Audio"}". Please try again.`
        )
      }
    } catch {
      if (playbackTokenRef.current === currentToken) {
        setActivePodcastId(null)
        setPlaybackError(
          `Failed to load podcast "${podcast.transcript?.title || podcast.document_title || "Audio"}".`
        )
      }
    } finally {
      if (playbackTokenRef.current === currentToken) {
        setLoadingId(null)
      }
    }
  }

  const effectiveDuration = (activePodcast && (tts.duration || activePodcast.duration)) || 0
  const isPreparing = tts.status === "preparing" || loadingId !== null

  return (
    <section className="podcast-gallery-section" aria-labelledby="podcast-gallery-heading">
      <div className="library-section-heading">
        <h2 id="podcast-gallery-heading">Podcasts</h2>
        {podcasts && podcasts.length > 0 && (
          <span className="library-result-count" aria-live="polite">
            {podcasts.length} {podcasts.length === 1 ? "podcast" : "podcasts"}
          </span>
        )}
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
        <div className="podcast-grid" aria-label="Loading podcasts" aria-busy="true">
          {Array.from({ length: 3 }, (_, index) => (
            <div key={index} className="library-card-skeleton">
              <div />
              <span />
              <span />
            </div>
          ))}
        </div>
      ) : isError ? (
        <div className="podcast-empty-state" role="alert">
          <AlertCircle size={32} />
          <h3>Podcasts could not be loaded</h3>
          <p>Check your connection and try again.</p>
          <button
            type="button"
            className="library-secondary-button"
            style={{ marginTop: "12px" }}
            onClick={() => void refetch()}
          >
            Try again
          </button>
        </div>
      ) : !podcasts || podcasts.length === 0 ? (
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
                    >
                      <BookOpen size={13} aria-hidden="true" />
                      <span>{bookTitle}</span>
                    </Link>
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

      {activePodcast && tts.status !== "idle" && (
        <div
          className="podcast-bottom-player"
          data-testid="bottom-player"
          role="region"
          aria-label="Audio player"
        >
          <div className="podcast-bottom-content">
            <div className="podcast-bottom-info">
              <div className="podcast-bottom-title">
                {activePodcast.transcript?.title ||
                  (activePodcast.scope === "section" && activePodcast.section_title
                    ? activePodcast.section_title
                    : activePodcast.document_title) ||
                  `Podcast ${activePodcast.id}`}
              </div>
              <div className="podcast-bottom-doc">
                {activePodcast.document_title || `Document ${activePodcast.document_id}`}
              </div>
            </div>

            <div className="podcast-bottom-controls">
              <button
                type="button"
                className="podcast-card-skip-btn"
                disabled={isPreparing}
                onClick={() => ttsEngine.skipSeconds(-15)}
                aria-label="Rewind 15 seconds"
                title="Rewind 15 seconds"
              >
                <RotateCcw size={16} />
              </button>
              <button
                type="button"
                className="podcast-play-btn"
                disabled={isPreparing}
                onClick={() => ttsEngine.toggle()}
                aria-label={tts.status === "playing" ? "Pause" : "Play"}
              >
                {isPreparing ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : tts.status === "playing" ? (
                  <Pause size={15} />
                ) : (
                  <Play size={15} style={{ marginLeft: "2px" }} />
                )}
              </button>
              <button
                type="button"
                className="podcast-card-skip-btn"
                disabled={isPreparing}
                onClick={() => ttsEngine.skipSeconds(15)}
                aria-label="Fast forward 15 seconds"
                title="Fast forward 15 seconds"
              >
                <RotateCw size={16} />
              </button>
            </div>

            <div className="podcast-bottom-track">
              <span className="podcast-time-label">{formatTime(tts.currentTime)}</span>
              <input
                type="range"
                disabled={isPreparing}
                min={0}
                max={effectiveDuration > 0 ? effectiveDuration : 100}
                step={0.5}
                value={tts.currentTime || 0}
                onChange={(event) => {
                  const newPosition = Number(event.target.value)
                  ttsEngine.seekTo(newPosition)
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                    event.preventDefault()
                    if (event.key === "ArrowLeft") ttsEngine.skipSeconds(-15)
                    if (event.key === "ArrowRight") ttsEngine.skipSeconds(15)
                  }
                }}
                className="podcast-range-slider"
                aria-label="Seek audio"
                aria-valuemin={0}
                aria-valuemax={Math.round(effectiveDuration)}
                aria-valuenow={Math.round(tts.currentTime)}
                aria-valuetext={`${formatTime(tts.currentTime)} of ${formatTime(effectiveDuration)}`}
              />
              <span className="podcast-time-label">
                {formatTime(effectiveDuration)}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <button
                type="button"
                className="podcast-card-skip-btn"
                disabled={isPreparing}
                onClick={() => {
                  const speedOptions = [0.75, 1, 1.25, 1.5, 2]
                  const nextIndex = (speedOptions.indexOf(tts.speed) + 1) % speedOptions.length
                  ttsEngine.setSpeed(speedOptions[nextIndex])
                }}
                aria-label={`Playback speed ${tts.speed}x`}
                title="Change speed"
              >
                <span style={{ fontSize: "11px", fontWeight: 600 }}>{tts.speed}x</span>
              </button>
              <button
                type="button"
                className="podcast-card-skip-btn"
                onClick={() => ttsEngine.toggleMute()}
                aria-label={tts.muted ? "Unmute audio" : "Mute audio"}
                title={tts.muted ? "Unmute" : "Mute"}
              >
                {tts.muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
              </button>
              <button
                type="button"
                className="podcast-card-skip-btn"
                onClick={() => {
                  ttsEngine.stop()
                  setActivePodcastId(null)
                }}
                aria-label="Stop audio"
                title="Stop"
              >
                <X size={16} />
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
