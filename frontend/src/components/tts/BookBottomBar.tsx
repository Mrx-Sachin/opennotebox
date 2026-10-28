"use client"

import { useEffect, useRef } from "react"
import {
  Loader2,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import NowReadingModal from "./NowReadingModal"

export default function BookBottomBar() {
  const activeReading = useReader((state) => state.activeReading)
  const expandedReading = useReader((state) => state.expandedReading)
  const setExpandedReading = useReader((state) => state.setExpandedReading)
  const selectedDocId = useReader((state) => state.selectedDocId)
  const activePodcast = useReader((state) => state.activePodcast)
  const tts = useReader((state) => state.tts)
  const playerRef = useRef<HTMLDivElement>(null)

  const isInsideSourceDoc = selectedDocId != null && activeReading != null && selectedDocId === activeReading.docId
  const visible = activeReading != null && tts.status !== "idle" && activePodcast == null && !isInsideSourceDoc

  useEffect(() => {
    const player = playerRef.current
    if (!visible || !player) return
    const updateHeight = () => {
      document.documentElement.style.setProperty(
        "--podcast-player-height",
        `${Math.ceil(player.getBoundingClientRect().height)}px`
      )
    }
    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(player)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty("--podcast-player-height")
    }
  }, [visible])

  if (!activeReading || tts.status === "idle" || activePodcast != null || isInsideSourceDoc) {
    return expandedReading ? (
      <NowReadingModal onClose={() => setExpandedReading(false)} />
    ) : null
  }

  const isPreparing = tts.status === "preparing"
  const isPlaying = tts.status === "playing"
  const displayTitle = activeReading.title || `Document ${activeReading.docId}`
  const currentPage = tts.page ?? 1
  const totalPages = activeReading.numPages || 1

  return (
    <>
      <div
        ref={playerRef}
        className="podcast-bottom-player book-bottom-player"
        data-testid="book-bottom-bar"
        role="region"
        aria-label="Book reading player"
      >
        <div className="book-bottom-content">
          <div className="book-bottom-main-row">
            <div className="book-bottom-meta">
              <button
                type="button"
                className="book-bottom-title-btn"
                onClick={() => setExpandedReading(true)}
                data-testid="book-bottom-title"
                aria-label={`Open now reading view for ${displayTitle}`}
                title="Open now reading view"
              >
                <span className="book-bottom-title-text">{displayTitle}</span>
              </button>
              <div className="book-bottom-badge-row">
                <span className="book-bottom-page-pill" data-testid="book-bottom-page">
                  Page {currentPage} of {totalPages}
                </span>
              </div>
            </div>

            <div className="book-bottom-actions">
              <button
                type="button"
                className="book-bottom-icon-btn book-skip-prev-btn"
                disabled={isPreparing || currentPage <= 1}
                onClick={() => ttsEngine.skipPage(-1)}
                aria-label="Previous page"
                title="Previous page"
                data-testid="book-bottom-prev-page"
              >
                <RotateCcw size={16} />
              </button>

              <button
                type="button"
                className="book-bottom-hero-play-btn"
                disabled={isPreparing}
                onClick={() => ttsEngine.toggle()}
                aria-label={isPlaying ? "Pause" : "Play"}
                data-testid="book-bottom-play-btn"
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
                className="book-bottom-icon-btn book-skip-next-btn"
                disabled={isPreparing || (activeReading.numPages > 0 && currentPage >= activeReading.numPages)}
                onClick={() => ttsEngine.skipPage(1)}
                aria-label="Next page"
                title="Next page"
                data-testid="book-bottom-next-page"
              >
                <RotateCw size={16} />
              </button>

              <button
                type="button"
                className="book-bottom-icon-btn book-bottom-expand-action"
                onClick={() => setExpandedReading(true)}
                aria-label="Expand now reading view"
                title="Expand now reading view"
                data-testid="book-bottom-expand-btn"
              >
                <span data-testid="book-bottom-expand" className="inline-flex items-center justify-center">
                  <Maximize2 size={16} />
                </span>
              </button>

              <button
                type="button"
                className="book-bottom-icon-btn book-desktop-only"
                disabled={isPreparing}
                onClick={() => {
                  const speedOptions = [0.75, 1, 1.25, 1.5, 2]
                  const nextIndex = (speedOptions.indexOf(tts.speed) + 1) % speedOptions.length
                  ttsEngine.setSpeed(speedOptions[nextIndex])
                }}
                aria-label={`Playback speed ${tts.speed}x`}
                title="Change speed"
                data-testid="book-bottom-speed-btn"
              >
                <span className="book-speed-badge">{tts.speed}x</span>
              </button>

              <button
                type="button"
                className="book-bottom-icon-btn book-desktop-only"
                onClick={() => ttsEngine.toggleMute()}
                aria-label={tts.muted ? "Unmute audio" : "Mute audio"}
                title={tts.muted ? "Unmute" : "Mute"}
                data-testid="book-bottom-mute-btn"
              >
                {tts.muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
              </button>

              <button
                type="button"
                className="book-bottom-icon-btn"
                onClick={() => ttsEngine.stop()}
                aria-label="Stop reading"
                title="Stop reading"
                data-testid="book-bottom-stop-btn"
              >
                <Square size={14} />
              </button>
            </div>
          </div>

          <div className="book-bottom-track-row" data-testid="book-bottom-track">
            <div className="book-sentence-progress-bar">
              <div
                className="book-sentence-progress-fill"
                style={{
                  width: `${
                    tts.sentenceCount > 0
                      ? Math.min(100, Math.round(((tts.sentenceIndex + 1) / tts.sentenceCount) * 100))
                      : 0
                  }%`,
                }}
              />
            </div>
            <div className="book-track-label-row">
              <span className="book-track-label">Sentence {tts.sentenceIndex + 1}</span>
              <span className="book-track-label">
                {tts.sentenceCount > 0 ? `${tts.sentenceCount} sentences` : ""}
              </span>
            </div>
          </div>
        </div>
      </div>

      {expandedReading && (
        <NowReadingModal onClose={() => setExpandedReading(false)} />
      )}
    </>
  )
}
