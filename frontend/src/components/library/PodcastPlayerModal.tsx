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
  Send,
  Sparkles,
  Square,
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

export default function PodcastPlayerModal({ podcast, onClose }: PodcastPlayerModalProps) {
  const tts = useReader((state) => state.tts)
  const activePodcast = useReader((state) => state.activePodcast)
  const isFullscreen = true
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

  // Interruption interactive state
  const [isInterruptionOpen, setIsInterruptionOpen] = useState(false)
  const [savedPlaybackTime, setSavedPlaybackTime] = useState<number>(0)
  const [questionText, setQuestionText] = useState("")
  const [isAnswering, setIsAnswering] = useState(false)
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
    setIsAnswering(false)
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
  }, [isFullscreen, isInterruptionOpen, isCurrentActive, togglePlayback, handleCloseInterruption, handleModalClose])



  const handleLineClick = (index: number) => {
    if (isCurrentActive && transcriptLines.length > 0 && effectiveDuration > 0) {
      const estimatedFraction = index / transcriptLines.length
      const targetTime = estimatedFraction * effectiveDuration
      if (isInterruptionOpen) {
        setSavedPlaybackTime(targetTime)
      } else {
        ttsEngine.seekTo(targetTime)
      }
    }
  }

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
    setIsAnswering(false)
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
      if (text) {
        setQuestionText(text)
        await handleSubmitQuestion(text)
      } else {
        setInterruptionError("No speech was recognized. Please try asking again or type your question.")
      }
    } catch (err) {
      console.error("Audio transcription failed:", err)
      setInterruptionError(
        err instanceof Error
          ? err.message
          : "Voice transcription failed. You can type your question instead."
      )
    } finally {
      setIsTranscribing(false)
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
    if (!query || isAnswering) return

    setIsAnswering(true)
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

      if (response.audio_url) {
        await ttsEngine.playInterruptionAnswer(response.audio_url, () => {
          setIsAnswering(false)
        })
      } else {
        setIsAnswering(false)
      }
    } catch (err) {
      console.error("Podcast interruption failed:", err)
      setInterruptionError(
        err instanceof Error ? err.message : "Failed to obtain answers from podcast hosts."
      )
      setIsAnswering(false)
    }
  }

  const isSpeakingHostA =
    isPlaying &&
    (activeLine?.speaker.toLowerCase().includes("host a") ||
      activeLine?.speaker.toLowerCase().includes("ryan") ||
      (!activeLine?.speaker.toLowerCase().includes("host b") &&
        !activeLine?.speaker.toLowerCase().includes("lessac")))
  const isSpeakingHostB =
    isPlaying &&
    (activeLine?.speaker.toLowerCase().includes("host b") ||
      activeLine?.speaker.toLowerCase().includes("lessac"))

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
              onClick={handleModalClose}
              aria-label="Minimize player"
              title="Minimize to bottom player"
              data-testid="podcast-modal-close"
            >
              <Minimize2 size={18} />
            </button>

            <button
              type="button"
              className="podcast-modal-action-btn"
              onClick={handleModalClose}
              aria-label="Close"
              title="Close player"
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
                <div
                  className={`podcast-host-card ${
                    isSpeakingHostA || (isAnswering && answerResponse != null) ? "is-speaking-host" : ""
                  }`}
                >
                  <div className="podcast-host-avatar host-a">
                    <span>A</span>
                    {(isSpeakingHostA || (isPlaying && isInterruptionOpen)) && (
                      <div className="podcast-speaking-pulse" />
                    )}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host A</span>
                    <span className="podcast-host-name">Ryan</span>
                  </div>
                </div>

                {!isInterruptionOpen ? (
                  <div className="podcast-visualizer-center">
                    <div className="podcast-wave-bars" aria-hidden="true">
                      {Array.from({ length: 7 }).map((_, index) => (
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

                    {/* NotebookLM Style 1-Tap Join Conversation Hero */}
                    <div className="mt-8 flex flex-col items-center gap-3 w-full max-w-sm">
                      <button
                        type="button"
                        onClick={() => handleOpenInterruption(true)}
                        className="w-full flex items-center justify-center gap-2.5 py-3 px-5 rounded-2xl bg-gradient-to-r from-indigo-600 via-indigo-500 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-semibold text-sm shadow-xl shadow-indigo-950/60 hover:shadow-indigo-900/80 hover:scale-[1.02] active:scale-[0.98] transition-all"
                      >
                        <span className="text-lg">✋</span>
                        <span>Join Conversation (Call In)</span>
                      </button>
                      <p className="text-[12px] text-zinc-400 text-center">
                        Tap to speak directly with the hosts about what you&apos;re hearing
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

                <div
                  className={`podcast-host-card ${
                    isSpeakingHostB || (isAnswering && answerResponse != null) ? "is-speaking-host" : ""
                  }`}
                >
                  <div className="podcast-host-avatar host-b">
                    <span>B</span>
                    {(isSpeakingHostB || (isPlaying && isInterruptionOpen)) && (
                      <div className="podcast-speaking-pulse" />
                    )}
                  </div>
                  <div className="podcast-host-meta">
                    <span className="podcast-host-role">Host B</span>
                    <span className="podcast-host-name">Lessac</span>
                  </div>
                </div>
              </div>

              {/* Interruption UI Body: NotebookLM Live Call-In */}
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
                        const isHostA =
                          !isUser &&
                          (line.speaker.toLowerCase().includes("host a") ||
                            line.speaker.toLowerCase().includes("ryan"))

                        if (isUser) {
                          return (
                            <div
                              key={idx}
                              className="p-3 rounded-xl border border-purple-500/40 bg-purple-950/40 text-xs flex flex-col gap-1 shadow-sm"
                            >
                              <div className="flex items-center gap-1.5 text-purple-300 font-bold text-[10px] uppercase tracking-wider">
                                <span>👤</span>
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
                              isHostA
                                ? "bg-indigo-950/30 border-indigo-500/40"
                                : "bg-emerald-950/30 border-emerald-500/40"
                            }`}
                          >
                            <span
                              className={`font-bold uppercase text-[10px] tracking-wider ${
                                isHostA ? "text-indigo-400" : "text-emerald-400"
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
                            <span>👤</span>
                            <span>You (Caller)</span>
                          </div>
                          <p className="text-purple-100 text-sm font-medium leading-relaxed">
                            &ldquo;{activeQuestion}&rdquo;
                          </p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* UNIFIED ACTIVE STATE AREA: WORKS SEAMLESSLY FOR FIRST QUESTION & FOLLOW-UPS */}
                  {isRecording ? (
                    /* 1. Live Recording State */
                    <div className="p-5 rounded-2xl bg-gradient-to-b from-rose-950/50 to-zinc-900 border border-rose-500/50 flex flex-col items-center gap-3.5 text-center shadow-xl shadow-rose-950/40">
                      <div className="flex items-center gap-2.5 text-rose-300 text-xs font-bold uppercase tracking-wider">
                        <span className="relative flex h-2.5 w-2.5">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500"></span>
                        </span>
                        <span>Live on Mic with Hosts — 0:{recordingSeconds.toString().padStart(2, "0")}</span>
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
                        Speak your question or thought — Ryan &amp; Lessac will react live.
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
                  ) : isAnswering ? (
                    /* 3. Generating Answer */
                    <div className="p-6 rounded-2xl bg-zinc-900/90 border border-zinc-800 flex flex-col items-center justify-center gap-3 text-center">
                      <Loader2 size={26} className="animate-spin text-indigo-400" />
                      <div className="text-sm font-semibold text-zinc-200">
                        Ryan and Lessac are answering...
                      </div>
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
                              : "Ask Ryan and Lessac a question..."
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
                            className="p-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 transition-colors"
                          >
                            <Send size={14} />
                          </button>
                        </div>
                      </div>

                      {/* Action Row: Talkback status and Return to show */}
                      <div className="flex items-center justify-between pt-2">
                        {answerResponse ? (
                          <div className="flex items-center gap-2 text-xs text-indigo-300 font-medium">
                            <Volume2 size={15} className="animate-pulse" />
                            <span>Host talkback active</span>
                          </div>
                        ) : (
                          <div className="text-xs text-zinc-400">
                            Speak or type to ask
                          </div>
                        )}
                        <button
                          type="button"
                          onClick={handleReturnToEpisode}
                          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-md active:scale-95"
                        >
                          <RotateCcw size={13} />
                          <span>Return to Show (Resume)</span>
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
                    const isHostA =
                      line.speaker.toLowerCase().includes("host a") ||
                      line.speaker.toLowerCase().includes("host_a") ||
                      line.speaker.toLowerCase().includes("ryan")
                    const isCurrentLine = activeLineIndex === index

                    return (
                      <div
                        key={index}
                        className={`podcast-transcript-card ${isHostA ? "is-host-a" : "is-host-b"} ${
                          isCurrentLine ? "!border-indigo-500 !bg-indigo-950/40 shadow-sm" : ""
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
                onClick={handleModalClose}
                title="Jump directly to source document reader"
              >
                <BookOpen size={15} />
                <span>Open in Reader</span>
              </Link>

              <button
                type="button"
                className={`podcast-modal-jump-btn !px-3.5 !py-1.5 ${
                  isInterruptionOpen
                    ? "!border-indigo-500 !bg-indigo-950/60 !text-white"
                    : "!border-indigo-500/40 !bg-indigo-950/30 text-indigo-300 hover:bg-indigo-900/40"
                }`}
                onClick={isInterruptionOpen ? handleCloseInterruption : () => handleOpenInterruption(true)}
                title={isInterruptionOpen ? "Leave Call-In" : "Join the conversation"}
                aria-label={isInterruptionOpen ? "Leave Call-In" : "Join Conversation"}
              >
                <span>✋</span>
                <span className="font-semibold">{isInterruptionOpen ? "Leave Call-In" : "Join Show"}</span>
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
