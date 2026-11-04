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
  PhoneOff,
  Play,
  Radio,
  RotateCcw,
  RotateCw,
  Send,
  Sparkles,
  Square,
  User,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import {
  type Podcast,
  type PodcastInterruptResponse,
  interruptPodcast,
  transcribePodcastAudio,
} from "@/services/api"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import { startRecording, type ActiveRecording } from "@/lib/audioRecorder"

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

export function isSpeakerHostA(speakerRaw?: string | null): boolean {
  if (!speakerRaw) return true
  const s = speakerRaw.trim().toLowerCase()
  if (
    s === "a" ||
    s.startsWith("a:") ||
    s.startsWith("a -") ||
    s.startsWith("a –") ||
    s.includes("host a") ||
    s.includes("host_a") ||
    s.includes("host-a") ||
    s.includes("hosta") ||
    s.includes("michael") ||
    s.includes("ryan")
  ) {
    return true
  }
  if (
    s === "b" ||
    s.startsWith("b:") ||
    s.startsWith("b -") ||
    s.startsWith("b –") ||
    s.includes("host b") ||
    s.includes("host_b") ||
    s.includes("host-b") ||
    s.includes("hostb") ||
    s.includes("bella") ||
    s.includes("lessac")
  ) {
    return false
  }
  return true
}

export function isSpeakerHostB(speakerRaw?: string | null): boolean {
  if (!speakerRaw) return false
  return !isSpeakerHostA(speakerRaw)
}

export default function PodcastPlayerModal({ podcast, onClose }: PodcastPlayerModalProps) {
  const tts = useReader((state) => state.tts)
  const activePodcast = useReader((state) => state.activePodcast)
  const [isFullscreen, setIsFullscreen] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("podcast_modal_fullscreen")
      if (saved !== null) return saved === "true"
    }
    return false
  })
  const [viewMode, setViewMode] = useState<"both" | "visualizer" | "transcript">("both")

  const toggleFullscreen = () => {
    setIsFullscreen((prev) => {
      const next = !prev
      if (typeof window !== "undefined") {
        localStorage.setItem("podcast_modal_fullscreen", String(next))
      }
      return next
    })
  }
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

  // Detect host names dynamically from transcript lines if present
  const hasLegacyNames = transcriptLines.some(
    (l) =>
      l.speaker.toLowerCase().includes("ryan") ||
      l.speaker.toLowerCase().includes("lessac")
  )
  const hostAName = hasLegacyNames ? "Ryan" : "Michael"
  const hostBName = hasLegacyNames ? "Lessac" : "Bella"

  // Interruption interactive state
  const [isInterruptionOpen, setIsInterruptionOpen] = useState(false)
  const [savedPlaybackTime, setSavedPlaybackTime] = useState<number>(0)
  const [questionText, setQuestionText] = useState("")
  const [isGenerating, setIsGenerating] = useState(false)
  const [isPlayingAnswer, setIsPlayingAnswer] = useState(false)
  const isAnswering = isGenerating || isPlayingAnswer
  const [isRecording, setIsRecording] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [answerResponse, setAnswerResponse] = useState<PodcastInterruptResponse | null>(null)
  const [interruptionError, setInterruptionError] = useState<string | null>(null)
  const [callHistory, setCallHistory] = useState<{ speaker: string; text: string }[]>([])
  const [activeQuestion, setActiveQuestion] = useState<string>("")
  const activeRecorderRef = useRef<ActiveRecording | null>(null)

  const activeLineIndex = (() => {
    if (transcriptLines.length === 0) return -1
    const targetTime = isInterruptionOpen ? savedPlaybackTime : currentTime
    for (let i = 0; i < transcriptLines.length; i++) {
      const line = transcriptLines[i]
      if (line.start_time != null && line.end_time != null) {
        if (targetTime >= line.start_time && targetTime <= line.end_time) {
          return i
        }
      }
    }
    for (let i = transcriptLines.length - 1; i >= 0; i--) {
      const line = transcriptLines[i]
      if (line.start_time != null && targetTime >= line.start_time) {
        return i
      }
    }
    if (effectiveDuration > 0) {
      const fraction = Math.max(0, Math.min(1, targetTime / effectiveDuration))
      const index = Math.floor(fraction * transcriptLines.length)
      return Math.min(index, transcriptLines.length - 1)
    }
    return -1
  })()

  const activeLine = activeLineIndex >= 0 ? transcriptLines[activeLineIndex] : null

  const isCurrentLineA = activeLine ? isSpeakerHostA(activeLine.speaker) : false
  const isCurrentLineB = activeLine ? isSpeakerHostB(activeLine.speaker) : false

  const isSpeakingHostA = isPlaying && isCurrentLineA
  const isSpeakingHostB = isPlaying && isCurrentLineB

  const togglePlayback = useCallback(() => {
    if (isCurrentActive) {
      ttsEngine.toggle()
    } else {
      void ttsEngine.playPodcast(podcast).catch(() => {})
    }
  }, [isCurrentActive, podcast])

  const handleModalClose = useCallback(async () => {
    if (activeRecorderRef.current) {
      await activeRecorderRef.current.cancel()
      activeRecorderRef.current = null
    }
    if (ttsEngine.isInterrupted()) {
      await ttsEngine.cancelInterruption()
    }
    onClose()
  }, [onClose])

  const handleCloseInterruption = useCallback(async () => {
    if (activeRecorderRef.current) {
      await activeRecorderRef.current.cancel()
      activeRecorderRef.current = null
    }
    setIsRecording(false)
    setIsTranscribing(false)
    if (ttsEngine.isInterrupted()) {
      await ttsEngine.cancelInterruption()
    }
    setIsInterruptionOpen(false)
    setIsGenerating(false)
    setIsPlayingAnswer(false)
    setAnswerResponse(null)
    setInterruptionError(null)
  }, [])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return

      if (["Escape", " ", "ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.stopImmediatePropagation()
      }
      if (event.key === "Escape") {
        event.preventDefault()
        if (isInterruptionOpen) {
          void handleCloseInterruption()
        } else {
          void handleModalClose()
        }
      } else if (event.key === " ") {
        event.preventDefault()
        togglePlayback()
      } else if (event.key === "ArrowLeft") {
        event.preventDefault()
        ttsEngine.skipSeconds(-15)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        ttsEngine.skipSeconds(15)
      }
    }
    window.addEventListener("keydown", handleKeyDown, true)
    return () => window.removeEventListener("keydown", handleKeyDown, true)
  }, [isFullscreen, isInterruptionOpen, isCurrentActive, togglePlayback, handleCloseInterruption, handleModalClose])

  const handleLineClick = async (index: number) => {
    if (index < 0 || index >= transcriptLines.length) return
    const line = transcriptLines[index]
    if (!line) return

    // Calculate target timestamp accurately from line start_time
    let targetTime: number
    if (line.start_time != null && isFinite(line.start_time)) {
      // Add a small 0.05s buffer into the turn so targetTime reliably lands inside this turn
      // rather than right on the preceding boundary or silence.
      targetTime = Math.max(0, line.start_time + 0.05)
    } else if (effectiveDuration > 0 && transcriptLines.length > 0) {
      targetTime = (index / transcriptLines.length) * effectiveDuration
    } else {
      targetTime = 0
    }

    // If studio call-in is open, update saved resume playback timestamp
    if (isInterruptionOpen) {
      setSavedPlaybackTime(targetTime)
      return
    }

    // If podcast is already active, seek directly to this line
    if (isCurrentActive) {
      ttsEngine.seekTo(targetTime)
    } else {
      // Start podcast playback directly from this line
      await ttsEngine.playPodcast(podcast, targetTime)
    }
  }

  // Scroll active transcript line smoothly into view
  useEffect(() => {
    if (activeLineIndex < 0) return
    const el = document.querySelector(`[data-testid="transcript-line-${activeLineIndex}"]`)
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [activeLineIndex])

  // Studio Call-In Interruption flow
  const handleOpenInterruption = async (startVoice = true) => {
    // Rewind to the beginning of the active sentence so resumption sounds natural!
    const lineStartTime = activeLine?.start_time != null ? activeLine.start_time : undefined
    const info = ttsEngine.pauseForInterruption(lineStartTime)
    setSavedPlaybackTime(info ? info.savedTime : currentTime)
    if (viewMode === "transcript") {
      setViewMode("both")
    }
    setIsInterruptionOpen(true)
    setInterruptionError(null)
    setAnswerResponse(null)
    setQuestionText("")
    setCallHistory([])

    if (startVoice) {
      try {
        const recorder = await startRecording()
        activeRecorderRef.current = recorder
        setIsRecording(true)
      } catch (err) {
        console.warn("Auto voice capture on join failed:", err)
      }
    }
  }

  const handleReturnToEpisode = async () => {
    if (activeRecorderRef.current) {
      await activeRecorderRef.current.cancel()
      activeRecorderRef.current = null
    }
    setIsRecording(false)
    setIsTranscribing(false)
    await ttsEngine.resumeFromInterruption(savedPlaybackTime)
    setIsInterruptionOpen(false)
    setIsGenerating(false)
    setIsPlayingAnswer(false)
    setAnswerResponse(null)
    setQuestionText("")
    setCallHistory([])
    setInterruptionError(null)
  }

  // Voice recording timer
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null
    if (isRecording) {
      timer = setInterval(() => {
        setRecordingSeconds((sec) => sec + 1)
      }, 1000)
    } else {
      setRecordingSeconds(0)
    }
    return () => {
      if (timer) clearInterval(timer)
    }
  }, [isRecording])

  useEffect(() => {
    return () => {
      if (activeRecorderRef.current) {
        void activeRecorderRef.current.cancel()
        activeRecorderRef.current = null
      }
    }
  }, [])

  const handleStartRecording = async () => {
    try {
      setInterruptionError(null)
      const recorder = await startRecording()
      activeRecorderRef.current = recorder
      setIsRecording(true)
    } catch (err) {
      console.error("Microphone capture failed:", err)
      setInterruptionError(
        err instanceof Error ? err.message : "Microphone access was denied or failed."
      )
    }
  }

  const handleStopRecording = async () => {
    const recorder = activeRecorderRef.current
    if (!recorder) return
    activeRecorderRef.current = null
    setIsRecording(false)
    setIsTranscribing(true)
    setInterruptionError(null)

    try {
      const audioBlob = await recorder.stop()
      const res = await transcribePodcastAudio(podcast.id, audioBlob)
      const text = res.transcript?.trim()
      setIsTranscribing(false)
      if (text) {
        setQuestionText(text)
        await handleSubmitQuestion(text)
      } else {
        setInterruptionError("No speech was recognized. Please try asking again or type your question.")
      }
    } catch (err) {
      console.error("Audio transcription failed:", err)
      setIsTranscribing(false)
      setInterruptionError(
        err instanceof Error
          ? err.message
          : "Voice transcription failed. You can type your question instead."
      )
    }
  }

  const handleCancelRecording = async () => {
    if (activeRecorderRef.current) {
      await activeRecorderRef.current.cancel()
      activeRecorderRef.current = null
    }
    setIsRecording(false)
    setRecordingSeconds(0)
  }

  const handleSubmitQuestion = async (explicitText?: string) => {
    const query = (explicitText ?? questionText).trim()
    if (!query || isGenerating || isPlayingAnswer) return

    setIsGenerating(true)
    setActiveQuestion(query)
    setInterruptionError(null)

    try {
      const response = await interruptPodcast(podcast.id, {
        question: query,
        current_time: savedPlaybackTime,
        line_index: activeLineIndex >= 0 ? activeLineIndex : undefined,
        history: callHistory.length > 0 ? callHistory : undefined,
      })
      setAnswerResponse(response)

      // Accumulate turns for continuous conversation / follow-ups
      const newTurns = [
        { speaker: "Listener", text: query },
        ...response.lines.map((l) => ({ speaker: l.speaker, text: l.text })),
      ]
      setCallHistory((prev) => [...prev, ...newTurns])

      if (response.resume_time != null) {
        setSavedPlaybackTime(response.resume_time)
      }
      setQuestionText("")
      setIsGenerating(false)

      if (response.audio_url) {
        setIsPlayingAnswer(true)
        await ttsEngine.playInterruptionAnswer(response.audio_url, () => {
          setIsPlayingAnswer(false)
        })
      }
    } catch (err) {
      console.error("Podcast interruption failed:", err)
      setInterruptionError(
        err instanceof Error ? err.message : "Failed to obtain answers from podcast hosts."
      )
      setIsGenerating(false)
      setIsPlayingAnswer(false)
    }
  }

  const isHostASpeakingEffective = isSpeakingHostA || (isAnswering && answerResponse != null)
  const isHostBSpeakingEffective = isSpeakingHostB

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
              <span>DeepRead Podcast</span>
            </div>
            <h2 className="podcast-modal-title" title={displayTitle}>
              {displayTitle}
            </h2>
            <Link
              href={`/dashboard/read/${podcast.document_id}`}
              className="podcast-modal-book-link"
              onClick={handleModalClose}
              title="Open full document reader"
              data-testid="podcast-modal-book-link"
            >
              <span>{podcast.document_title || `Doc #${podcast.document_id}`}</span>
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
              onClick={handleModalClose}
              aria-label="Minimize player"
              title="Minimize to bottom player"
              data-testid="podcast-modal-close"
            >
              <X size={18} />
            </button>
          </div>
        </header>

        {/* Content Body */}
        <div className={`podcast-modal-body ${viewMode === "both" ? "has-split-layout" : ""}`}>
          {/* Visualizer Panel / Interruption Panel */}
          {(viewMode === "both" || viewMode === "visualizer") && (
            <div
              className={`podcast-visualizer-pane ${viewMode === "visualizer" ? "is-centered" : ""}`}
              style={
                isInterruptionOpen
                  ? {
                      justifyContent: "flex-start",
                      padding: "20px 24px",
                      overflowY: "auto",
                    }
                  : undefined
              }
              data-testid="podcast-visualizer-pane"
            >
              {/* Hosts Row */}
              <div className="podcast-hosts-row">
                {/* Host A Card */}
                <div
                  className={`podcast-host-card ${
                    isHostASpeakingEffective ? "is-speaking" : ""
                  }`}
                >
                  <div className="podcast-host-avatar host-a">
                    <span>A</span>
                    {isHostASpeakingEffective && (
                      <>
                        <div className="podcast-speaking-pulse pulse-host-a ring-1" />
                        <div className="podcast-speaking-pulse pulse-host-a ring-2" />
                        <div className="podcast-host-live-badge badge-host-a" title="Host A On Air">
                          <span className="live-bar bar-1" />
                          <span className="live-bar bar-2" />
                          <span className="live-bar bar-3" />
                        </div>
                      </>
                    )}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host A</span>
                    <span className="podcast-host-name">{hostAName}</span>
                  </div>
                </div>

                {!isInterruptionOpen ? (
                  <div className="podcast-visualizer-center">
                    <div
                      className={`podcast-waveform ${
                        isSpeakingHostA
                          ? "speaker-a"
                          : isSpeakingHostB
                          ? "speaker-b"
                          : ""
                      }`}
                      data-testid="podcast-waveform"
                      aria-hidden="true"
                    >
                      {Array.from({ length: 7 }).map((_, index) => (
                        <span
                          key={index}
                          className={`podcast-wave-bar ${
                            isPreparing
                              ? "is-preparing"
                              : isPlaying
                              ? "is-animating"
                              : "is-paused"
                          }`}
                          style={{
                            animationDelay: `${(index % 7) * 0.12}s`,
                          }}
                        />
                      ))}
                    </div>

                    <div className="podcast-visualizer-status-wrap">
                      <span
                        className={`podcast-status-dot ${
                          isPreparing
                            ? "dot-preparing"
                            : !isPlaying
                            ? "dot-paused"
                            : isSpeakingHostA
                            ? "dot-host-a"
                            : isSpeakingHostB
                            ? "dot-host-b"
                            : "dot-playing"
                        }`}
                      />
                      <span className="podcast-visualizer-status">
                        {isPreparing
                          ? "Loading episode audio..."
                          : isPlaying
                          ? isSpeakingHostA
                            ? `${hostAName} speaking`
                            : isSpeakingHostB
                            ? `${hostBName} speaking`
                            : "Now playing"
                          : "Paused"}
                      </span>
                    </div>

                    {/* Redesigned Clean Studio Call-In Hero */}
                    <div className="podcast-callin-hero">
                      <button
                        type="button"
                        onClick={() => handleOpenInterruption(true)}
                        className="podcast-callin-btn"
                        title="Join conversation and talk directly with the hosts"
                      >
                        <div className="podcast-callin-btn-glow" aria-hidden="true" />
                        <span className="podcast-callin-icon-wrap">
                          <Radio size={15} className="podcast-callin-icon" />
                          <span className="podcast-callin-live-dot" />
                        </span>
                        <span className="podcast-callin-text">Join Conversation</span>
                        <span className="podcast-callin-tag">Studio Call-In</span>
                      </button>
                      <p className="podcast-callin-hint">
                        Speak directly with the hosts about what you&apos;re hearing
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center gap-1">
                    <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-950/60 border border-indigo-500/40 text-indigo-300 text-[11px] font-semibold tracking-wide uppercase">
                      <Sparkles size={12} className="text-indigo-400" />
                      <span>Interactive Discussion</span>
                    </div>
                    <span className="text-[11px] text-zinc-400">Ask the hosts anything</span>
                  </div>
                )}

                {/* Host B Card */}
                <div
                  className={`podcast-host-card ${
                    isHostBSpeakingEffective ? "is-speaking" : ""
                  }`}
                >
                  <div className="podcast-host-avatar host-b">
                    <span>B</span>
                    {isHostBSpeakingEffective && (
                      <>
                        <div className="podcast-speaking-pulse pulse-host-b ring-1" />
                        <div className="podcast-speaking-pulse pulse-host-b ring-2" />
                        <div className="podcast-host-live-badge badge-host-b" title="Host B On Air">
                          <span className="live-bar bar-1" />
                          <span className="live-bar bar-2" />
                          <span className="live-bar bar-3" />
                        </div>
                      </>
                    )}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host B</span>
                    <span className="podcast-host-name">{hostBName}</span>
                  </div>
                </div>
              </div>

              {/* Interruption UI Body: Studio Live Call-In */}
              {isInterruptionOpen && (
                <div className="w-full max-w-xl flex flex-col gap-3.5 mt-5">
                  {/* Caller Status & Context Header */}
                  <div className="flex items-center justify-between px-3.5 py-2.5 rounded-xl bg-zinc-900/90 border border-zinc-800">
                    <div className="flex items-center gap-2.5">
                      <span className="relative flex h-2.5 w-2.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500"></span>
                      </span>
                      <span className="text-xs font-bold uppercase tracking-wider text-rose-300">
                        Live Call-In
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-zinc-400">
                      <span>Resumes from:</span>
                      <span className="text-indigo-400 font-semibold">{formatTime(savedPlaybackTime)}</span>
                    </div>
                  </div>

                  {/* Active Context Quote (before interruption) */}
                  {activeLine && (
                    <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800/80 text-xs text-zinc-300 italic">
                      <span className="font-semibold text-indigo-400 not-italic mr-1.5">
                        {activeLine.speaker}:
                      </span>
                      &ldquo;{activeLine.text}&rdquo;
                    </div>
                  )}

                  {/* Conversation History: You (Caller) + Host Answers */}
                  {(callHistory.length > 0 || (isAnswering && activeQuestion)) && (
                    <div className="flex flex-col gap-2.5 max-h-60 overflow-y-auto reader-scroll pr-1">
                      {callHistory.map((line, idx) => {
                        const isUser =
                          line.speaker.toLowerCase().includes("you") ||
                          line.speaker.toLowerCase().includes("caller") ||
                          line.speaker.toLowerCase().includes("listener")
                        const isHostALine = !isUser && isSpeakerHostA(line.speaker)

                        if (isUser) {
                          return (
                            <div
                              key={idx}
                              className="p-3 rounded-xl border border-purple-500/40 bg-purple-950/40 text-xs flex flex-col gap-1 shadow-sm"
                            >
                              <div className="flex items-center gap-1.5 text-purple-300 font-bold text-[10px] uppercase tracking-wider">
                                <User size={12} className="text-purple-400" />
                                <span>You (Caller)</span>
                              </div>
                              <p className="text-purple-100 text-sm font-medium leading-relaxed">
                                &ldquo;{line.text}&rdquo;
                              </p>
                            </div>
                          )
                        }

                        return (
                          <div
                            key={idx}
                            className={`p-3.5 rounded-xl border text-xs flex flex-col gap-1 ${
                              isHostALine
                                ? "bg-indigo-950/30 border-indigo-500/40"
                                : "bg-emerald-950/30 border-emerald-500/40"
                            }`}
                          >
                            <span
                              className={`font-bold uppercase text-[10px] tracking-wider ${
                                isHostALine ? "text-indigo-400" : "text-emerald-400"
                              }`}
                            >
                              {line.speaker}
                            </span>
                            <p className="text-zinc-200 text-sm leading-relaxed">{line.text}</p>
                          </div>
                        )
                      })}

                      {/* While answering, show user's current question at the bottom */}
                      {isAnswering && activeQuestion && callHistory.every((t) => t.text !== activeQuestion) && (
                        <div className="p-3 rounded-xl border border-purple-500/40 bg-purple-950/40 text-xs flex flex-col gap-1 shadow-sm">
                          <div className="flex items-center gap-1.5 text-purple-300 font-bold text-[10px] uppercase tracking-wider">
                            <User size={12} className="text-purple-400" />
                            <span>You (Caller)</span>
                          </div>
                          <p className="text-purple-100 text-sm font-medium leading-relaxed">
                            &ldquo;{activeQuestion}&rdquo;
                          </p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Primary Interaction Area */}
                  {isRecording ? (
                    /* 1. Live Recording State */
                    <div className="p-5 rounded-2xl bg-gradient-to-b from-rose-950/50 to-zinc-900 border border-rose-500/50 flex flex-col items-center gap-3.5 text-center shadow-xl shadow-rose-950/40">
                      <div className="flex items-center gap-2.5 text-rose-300 text-xs font-bold uppercase tracking-wider">
                        <span className="relative flex h-2.5 w-2.5">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500"></span>
                        </span>
                        <span>Live on Mic with Hosts - 0:{recordingSeconds.toString().padStart(2, "0")}</span>
                      </div>

                      <div className="flex items-center gap-1.5 h-10 my-1">
                        {Array.from({ length: 9 }).map((_, i) => (
                          <span
                            key={i}
                            className="w-1.5 rounded-full bg-rose-500 animate-pulse"
                            style={{
                              height: `${30 + Math.sin(i * 0.9 + recordingSeconds) * 20}%`,
                              animationDuration: `${0.35 + (i % 3) * 0.15}s`,
                            }}
                          />
                        ))}
                      </div>

                      <p className="text-xs text-zinc-300">
                        Speak your question or thought - {hostAName} &amp; {hostBName} will react live.
                      </p>

                      <div className="flex items-center gap-3 mt-1">
                        <button
                          type="button"
                          onClick={handleStopRecording}
                          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-semibold text-xs shadow-lg shadow-rose-950/60 active:scale-95 transition-all"
                        >
                          <Square size={13} fill="currentColor" />
                          <span>Done Speaking (Talk to Hosts)</span>
                        </button>
                        <button
                          type="button"
                          onClick={handleCancelRecording}
                          className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : isTranscribing ? (
                    /* 2. Transcribing with Meta Muse */
                    <div className="p-6 rounded-2xl bg-zinc-900/90 border border-zinc-800 flex flex-col items-center justify-center gap-3 text-center">
                      <Loader2 size={26} className="animate-spin text-amber-400" />
                      <div className="text-sm font-semibold text-zinc-200">
                        Transcribing voice with Meta Muse...
                      </div>
                    </div>
                  ) : isGenerating ? (
                    /* 3. Generating Answer */
                    <div className="p-6 rounded-2xl bg-zinc-900/90 border border-zinc-800 flex flex-col items-center justify-center gap-3 text-center">
                      <Loader2 size={26} className="animate-spin text-indigo-400" />
                      <div className="text-sm font-semibold text-zinc-200">
                        {hostAName} and {hostBName} are preparing their response...
                      </div>
                      <p className="text-xs text-zinc-400">
                        Consulting source material and preparing dialogue
                      </p>
                    </div>
                  ) : isPlayingAnswer ? (
                    /* 3b. Playing Answer Audio */
                    <div className="p-5 rounded-2xl bg-gradient-to-b from-indigo-950/40 to-zinc-900 border border-indigo-500/40 flex flex-col items-center justify-center gap-2.5 text-center shadow-lg">
                      <div className="flex items-center gap-2 text-indigo-300 text-xs font-bold uppercase tracking-wider">
                        <span className="relative flex h-2 w-2">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                          <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                        </span>
                        <span>{hostAName} &amp; {hostBName} are on the air answering live</span>
                      </div>
                      <div className="flex items-center gap-1.5 h-6 my-1">
                        {Array.from({ length: 9 }).map((_, i) => (
                          <span
                            key={i}
                            className="w-1 rounded-full bg-indigo-400 animate-pulse"
                            style={{
                              height: `${40 + Math.sin(i * 0.9) * 35}%`,
                              animationDuration: `${0.35 + (i % 3) * 0.15}s`,
                            }}
                          />
                        ))}
                      </div>
                      <p className="text-xs text-zinc-400">
                        Listen to the hosts&apos; response or prepare a follow-up.
                      </p>
                    </div>
                  ) : (
                    /* 4. Ready for Input (Initial question OR follow-up) */
                    <div className="flex flex-col gap-2.5 pt-1">
                      {/* Quick Chips */}
                      <div className="flex flex-wrap gap-1.5">
                        {(answerResponse
                          ? [
                              "Can you explain that in more detail?",
                              "Why is that the case?",
                              "Give a concrete example",
                            ]
                          : [
                              "I don't understand that",
                              "Explain in simpler terms",
                              "Give a concrete example",
                            ]
                        ).map((chip) => (
                          <button
                            key={chip}
                            type="button"
                            onClick={() => {
                              setQuestionText(chip)
                              void handleSubmitQuestion(chip)
                            }}
                            className="px-2.5 py-1 text-[11px] rounded-full bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700/60 transition-colors"
                          >
                            {chip}
                          </button>
                        ))}
                      </div>

                      {/* Input Box with Microphone and Send Buttons */}
                      <div className="relative flex items-center">
                        <input
                          type="text"
                          value={questionText}
                          onChange={(e) => setQuestionText(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault()
                              void handleSubmitQuestion()
                            }
                          }}
                          placeholder={
                            answerResponse
                              ? "Ask a follow-up to the hosts..."
                              : `Ask ${hostAName} and ${hostBName} a question...`
                          }
                          className="w-full pl-3.5 pr-20 py-2.5 rounded-xl bg-zinc-900 border border-zinc-700/80 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
                        />
                        <div className="absolute right-1.5 flex items-center gap-1">
                          <button
                            type="button"
                            onClick={handleStartRecording}
                            title="Speak question via Meta Muse"
                            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                          >
                            <Mic size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSubmitQuestion()}
                            disabled={!questionText.trim()}
                            title="Submit question"
                            className="p-1.5 rounded-lg text-indigo-400 hover:text-indigo-300 hover:bg-zinc-800 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                          >
                            <Send size={15} />
                          </button>
                        </div>
                      </div>

                      {/* Return to episode action */}
                      <div className="flex items-center justify-between pt-1">
                        <span className="text-[11px] text-zinc-400">
                          {answerResponse ? "Satisfied with the answer?" : "Done asking questions?"}
                        </span>
                        <button
                          type="button"
                          onClick={handleReturnToEpisode}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
                        >
                          <span>Resume Episode ({formatTime(savedPlaybackTime)})</span>
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Error banner */}
                  {interruptionError && (
                    <div className="p-2.5 rounded-xl bg-red-950/40 border border-red-500/30 flex items-center gap-2 text-xs text-red-200">
                      <AlertCircle size={14} className="text-red-400 shrink-0" />
                      <span>{interruptionError}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Transcript Panel (Right Stage) */}
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
                <div className="podcast-transcript-list reader-scroll" tabIndex={0} aria-label="Transcript dialogue lines">
                  {transcriptLines.map((line, index) => {
                    const isHostA = isSpeakerHostA(line.speaker)
                    const isCurrentLine = activeLineIndex === index

                    return (
                      <div
                        key={index}
                        className={`podcast-transcript-card ${
                          isHostA ? "is-host-a" : "is-host-b"
                        } ${
                          isCurrentLine ? (isHostA ? "is-active-host-a" : "is-active-host-b") : ""
                        }`}
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
                            <span className="podcast-speaker-pill-dot" />
                            <span className="font-semibold">{isHostA ? "Host A" : "Host B"}</span>
                            <span className="podcast-speaker-pill-name">· {isHostA ? hostAName : hostBName}</span>
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
                onClick={handleModalClose}
                title="Jump directly to source document reader"
              >
                <BookOpen size={15} />
                <span>Open in Reader</span>
              </Link>

              {/* Redesigned Studio Call-In Button in Footer */}
              <button
                type="button"
                className={`podcast-footer-callin-btn ${isInterruptionOpen ? "is-active" : ""}`}
                onClick={isInterruptionOpen ? handleCloseInterruption : () => handleOpenInterruption(true)}
                title={isInterruptionOpen ? "Leave live call-in and resume episode" : "Join conversation with hosts"}
                aria-label={isInterruptionOpen ? "Leave Call-In" : "Join Conversation"}
              >
                {isInterruptionOpen ? (
                  <>
                    <PhoneOff size={14} className="podcast-footer-callin-icon" />
                    <span>Leave Call-In</span>
                  </>
                ) : (
                  <>
                    <Mic size={14} className="podcast-footer-callin-icon" />
                    <span>Join Conversation</span>
                  </>
                )}
              </button>
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
