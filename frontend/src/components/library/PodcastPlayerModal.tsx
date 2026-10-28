"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import {
  Activity,
  AlertCircle,
  BookOpen,
  FileText,
  Loader2,
  Maximize2,
  Mic,
  Minimize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import type { Podcast } from "@/services/api"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"

interface PodcastPlayerModalProps {
  podcast: Podcast
  onClose: () => void
}

function formatTime(seconds?: number | null): string {
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return "0:00"
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`
}

export default function PodcastPlayerModal({ podcast, onClose }: PodcastPlayerModalProps) {
  const tts = useReader((state) => state.tts)
  const activePodcast = useReader((state) => state.activePodcast)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [viewMode, setViewMode] = useState<"both" | "visualizer" | "transcript">("both")
  const containerRef = useRef<HTMLDivElement>(null)

  const isCurrentActive = activePodcast?.id === podcast.id
  const isPreparing = isCurrentActive && tts.status === "preparing"
  const isPlaying = isCurrentActive && tts.status === "playing"
  const currentTime = isCurrentActive ? tts.currentTime : 0
  const effectiveDuration = (isCurrentActive && tts.duration) || podcast.duration || 0

  const displayTitle =
    podcast.transcript?.title ||
    (podcast.scope === "section" && podcast.section_title
      ? podcast.section_title
      : podcast.document_title) ||
    `Podcast ${podcast.id}`

  const transcriptLines = podcast.transcript?.lines || []

  const togglePlayback = useCallback(() => {
    if (isCurrentActive) {
      ttsEngine.toggle()
    } else {
      void ttsEngine.playPodcast(podcast).catch(() => {})
    }
  }, [isCurrentActive, podcast])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (["Escape", " ", "ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.stopImmediatePropagation()
      }
      if (event.key === "Escape") {
        event.preventDefault()
        if (isFullscreen) {
          setIsFullscreen(false)
        } else {
          onClose()
        }
      } else if (event.key === " ") {
        event.preventDefault()
        togglePlayback()
      } else if (event.key === "ArrowLeft" && isCurrentActive) {
        event.preventDefault()
        ttsEngine.skipSeconds(-15)
      } else if (event.key === "ArrowRight" && isCurrentActive) {
        event.preventDefault()
        ttsEngine.skipSeconds(15)
      }
    }
    window.addEventListener("keydown", handleKeyDown, true)
    return () => window.removeEventListener("keydown", handleKeyDown, true)
  }, [isFullscreen, onClose, isCurrentActive, togglePlayback])

  const toggleFullscreen = () => {
    setIsFullscreen((prev) => !prev)
  }

  const handleLineClick = (index: number) => {
    if (isCurrentActive && transcriptLines.length > 0 && effectiveDuration > 0) {
      const estimatedFraction = index / transcriptLines.length
      ttsEngine.seekTo(estimatedFraction * effectiveDuration)
    }
  }

  return (
    <div
      ref={containerRef}
      className={`podcast-modal-overlay ${isFullscreen ? "is-fullscreen-mode" : ""}`}
      data-testid="podcast-player-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`Podcast Player: ${displayTitle}`}
    >
      <div className={`podcast-modal-container ${isFullscreen ? "is-fullscreen-container" : ""}`}>
        {/* Header */}
        <header className="podcast-modal-header">
          <div className="podcast-modal-header-info">
            <div className="podcast-modal-badge">
              <Mic size={14} />
              <span>Audio Discussion</span>
            </div>
            <h2 className="podcast-modal-title" title={displayTitle}>
              {displayTitle}
            </h2>
            <Link
              href={`/dashboard/read/${podcast.document_id}`}
              className="podcast-modal-book-link"
              onClick={onClose}
              title="Open source book in reader"
              data-testid="podcast-modal-book-link"
            >
              <BookOpen size={14} />
              <span>Go to book page ({podcast.document_title || `Document ${podcast.document_id}`})</span>
            </Link>
          </div>

          <div className="podcast-modal-header-actions">
            <div className="podcast-modal-view-toggles" role="group" aria-label="Display mode">
              <button
                type="button"
                className={`podcast-modal-toggle-btn ${viewMode === "both" ? "is-active" : ""}`}
                onClick={() => setViewMode("both")}
                title="Visualizer and Transcript"
              >
                <Activity size={14} />
                <FileText size={14} />
                <span>Both</span>
              </button>
              <button
                type="button"
                className={`podcast-modal-toggle-btn ${viewMode === "visualizer" ? "is-active" : ""}`}
                onClick={() => setViewMode("visualizer")}
                title="Visualizer only"
              >
                <Activity size={14} />
                <span>Visuals</span>
              </button>
              <button
                type="button"
                className={`podcast-modal-toggle-btn ${viewMode === "transcript" ? "is-active" : ""}`}
                onClick={() => setViewMode("transcript")}
                title="Transcript only"
              >
                <FileText size={14} />
                <span>Transcript</span>
              </button>
            </div>

            <button
              type="button"
              className="podcast-modal-action-btn"
              onClick={toggleFullscreen}
              aria-label={isFullscreen ? "Exit full screen" : "Enter full screen"}
              title={isFullscreen ? "Exit full screen" : "Full screen"}
              data-testid="podcast-fullscreen-toggle"
            >
              {isFullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
            </button>

            <button
              type="button"
              className="podcast-modal-action-btn"
              onClick={onClose}
              aria-label="Minimize player"
              title="Minimize to bottom bar"
              data-testid="podcast-modal-close"
            >
              <X size={18} />
            </button>
          </div>
        </header>

        {/* Content Body */}
        <div className={`podcast-modal-body ${viewMode === "both" ? "has-split-layout" : ""}`}>
          {/* Visualizer Panel */}
          {(viewMode === "both" || viewMode === "visualizer") && (
            <div
              className={`podcast-visualizer-pane ${viewMode === "visualizer" ? "is-centered" : ""}`}
              data-testid="podcast-visualizer-pane"
            >
              <div className="podcast-hosts-row">
                <div className={`podcast-host-card ${isPlaying ? "is-speaking-host" : ""}`}>
                  <div className="podcast-host-avatar host-a">
                    <span>A</span>
                    {isPlaying && <div className="podcast-speaking-pulse" />}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host A</span>
                    <span className="podcast-host-name">Ryan</span>
                  </div>
                </div>

                <div className="podcast-visualizer-center">
                  <div className="podcast-waveform" data-testid="podcast-waveform">
                    {Array.from({ length: 28 }).map((_, index) => (
                      <span
                        key={index}
                        className={`podcast-wave-bar ${isPlaying ? "is-animating" : ""}`}
                        style={{
                          animationDelay: `${(index % 7) * 0.12}s`,
                          height: isPlaying ? undefined : `${20 + (index % 5) * 8}%`,
                        }}
                      />
                    ))}
                  </div>
                  <span className="podcast-visualizer-status">
                    {isPreparing
                      ? "Loading episode audio..."
                      : isPlaying
                      ? "Now playing"
                      : "Paused"}
                  </span>
                </div>

                <div className={`podcast-host-card ${isPlaying ? "is-speaking-host" : ""}`}>
                  <div className="podcast-host-avatar host-b">
                    <span>B</span>
                    {isPlaying && <div className="podcast-speaking-pulse" />}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host B</span>
                    <span className="podcast-host-name">Lessac</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Transcript Panel */}
          {(viewMode === "both" || viewMode === "transcript") && (
            <div
              className={`podcast-transcript-pane ${viewMode === "transcript" ? "is-fullwidth" : ""}`}
              data-testid="podcast-transcript-pane"
            >
              <div className="podcast-transcript-header">
                <h3>Dialogue Transcript</h3>
                <span className="podcast-transcript-count">
                  {transcriptLines.length} {transcriptLines.length === 1 ? "turn" : "turns"}
                </span>
              </div>

              {transcriptLines.length === 0 ? (
                <div className="podcast-transcript-empty">
                  <FileText size={28} />
                  <p>Transcript is not available for this episode.</p>
                </div>
              ) : (
                <div className="podcast-transcript-list" tabIndex={0} aria-label="Transcript dialogue lines">
                  {transcriptLines.map((line, index) => {
                    const isHostA =
                      line.speaker.toLowerCase().includes("host a") ||
                      line.speaker.toLowerCase().includes("host_a") ||
                      line.speaker.toLowerCase().includes("ryan")
                    return (
                      <div
                        key={index}
                        className={`podcast-transcript-card ${isHostA ? "is-host-a" : "is-host-b"}`}
                        onClick={() => handleLineClick(index)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault()
                            handleLineClick(index)
                          }
                        }}
                        title="Click to jump to this point in audio"
                        data-testid={`transcript-line-${index}`}
                      >
                        <div className="podcast-transcript-speaker-row">
                          <span className={`podcast-speaker-pill ${isHostA ? "pill-host-a" : "pill-host-b"}`}>
                            {line.speaker}
                          </span>
                          <span className="podcast-line-index">Turn #{index + 1}</span>
                        </div>
                        <p className="podcast-transcript-text">{line.text}</p>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer Player Controls */}
        <footer className="podcast-modal-footer">
          {/* Progress Slider */}
          <div className="podcast-modal-track-row">
            <span className="podcast-modal-time">{formatTime(currentTime)}</span>
            <input
              type="range"
              disabled={isPreparing || !isCurrentActive}
              min={0}
              max={effectiveDuration > 0 ? effectiveDuration : 100}
              step={0.5}
              value={currentTime || 0}
              onChange={(event) => {
                const newPosition = Number(event.target.value)
                ttsEngine.seekTo(newPosition)
              }}
              className="podcast-range-slider"
              aria-label="Seek podcast audio"
              aria-valuemin={0}
              aria-valuemax={Math.round(effectiveDuration)}
              aria-valuenow={Math.round(currentTime)}
              aria-valuetext={`${formatTime(currentTime)} of ${formatTime(effectiveDuration)}`}
            />
            <span className="podcast-modal-time">{formatTime(effectiveDuration)}</span>
          </div>

          <div className="podcast-modal-controls-row">
            <div className="podcast-modal-controls-left">
              <Link
                href={`/dashboard/read/${podcast.document_id}`}
                className="podcast-modal-jump-btn"
                onClick={onClose}
                title="Jump directly to source document reader"
              >
                <BookOpen size={15} />
                <span>Open in Reader</span>
              </Link>
            </div>

            <div className="podcast-modal-controls-center">
              <button
                type="button"
                className="podcast-card-skip-btn"
                disabled={isPreparing || !isCurrentActive}
                onClick={() => ttsEngine.skipSeconds(-15)}
                aria-label="Rewind 15 seconds"
                title="Rewind 15 seconds"
              >
                <RotateCcw size={18} />
              </button>

              <button
                type="button"
                className="podcast-modal-play-btn"
                disabled={isPreparing}
                onClick={togglePlayback}
                aria-label={isPlaying ? "Pause" : "Play"}
                data-testid="podcast-modal-play-btn"
              >
                {isPreparing ? (
                  <Loader2 size={18} className="animate-spin" />
                ) : isPlaying ? (
                  <Pause size={18} />
                ) : (
                  <Play size={18} style={{ marginLeft: "2px" }} />
                )}
              </button>

              <button
                type="button"
                className="podcast-card-skip-btn"
                disabled={isPreparing || !isCurrentActive}
                onClick={() => ttsEngine.skipSeconds(15)}
                aria-label="Fast forward 15 seconds"
                title="Fast forward 15 seconds"
              >
                <RotateCw size={18} />
              </button>
            </div>

            <div className="podcast-modal-controls-right">
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
                <span style={{ fontSize: "12px", fontWeight: 600 }}>{tts.speed}x</span>
              </button>

              <button
                type="button"
                className="podcast-card-skip-btn"
                onClick={() => ttsEngine.toggleMute()}
                aria-label={tts.muted ? "Unmute audio" : "Mute audio"}
                title={tts.muted ? "Unmute" : "Mute"}
              >
                {tts.muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
              </button>
            </div>
          </div>
        </footer>
      </div>
    </div>
  )
}
