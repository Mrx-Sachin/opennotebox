"use client"

import { useEffect, useRef } from "react"
import {
  Loader2,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import PodcastPlayerModal from "./PodcastPlayerModal"

function formatTime(seconds?: number | null): string {
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return "0:00"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export default function PodcastBottomBar() {
  const activePodcast = useReader((state) => state.activePodcast)
  const expandedPodcast = useReader((state) => state.expandedPodcast)
  const setExpandedPodcast = useReader((state) => state.setExpandedPodcast)
  const tts = useReader((state) => state.tts)
  const playerRef = useRef<HTMLDivElement>(null)
  const visible = activePodcast != null && tts.status !== "idle"

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

  if (!activePodcast || tts.status === "idle") {
    return expandedPodcast ? (
      <PodcastPlayerModal
        podcast={expandedPodcast}
        onClose={() => setExpandedPodcast(null)}
      />
    ) : null
  }

  const isPreparing = tts.status === "preparing"
  const isPlaying = tts.status === "playing"
  const effectiveDuration = tts.duration || activePodcast.duration || 0

  const displayTitle =
    activePodcast.transcript?.title ||
    (activePodcast.scope === "section" && activePodcast.section_title
      ? activePodcast.section_title
      : activePodcast.document_title) ||
    `Podcast ${activePodcast.id}`

  return (
    <>
      <div
        ref={playerRef}
        className="podcast-bottom-player"
        data-testid="bottom-player"
        role="region"
        aria-label="Audio player"
      >
        <div className="podcast-bottom-content">
          <div className="podcast-bottom-info">
            <div className="podcast-bottom-title">
              {displayTitle}
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
              aria-label={isPlaying ? "Pause" : "Play"}
            >
              {isPreparing ? (
                <Loader2 size={15} className="animate-spin" />
              ) : isPlaying ? (
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
            <span className="podcast-time-label">
              {formatTime(tts.currentTime)}
            </span>
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
              onClick={() => setExpandedPodcast(activePodcast)}
              aria-label="Expand player and transcript"
              title="Expand full player and transcript"
              data-testid="bottom-player-expand"
            >
              <Maximize2 size={16} />
            </button>
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
              }}
              aria-label="Stop audio"
              title="Stop"
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>

      {expandedPodcast && (
        <PodcastPlayerModal
          podcast={expandedPodcast}
          onClose={() => setExpandedPodcast(null)}
        />
      )}
    </>
  )
}
