"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import {
  BookOpen,
  ChevronDown,
  ExternalLink,
  Loader2,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Square,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import { getPage } from "@/services/api"
import { buildPageSentences } from "@/lib/narrationFilter"

interface NowReadingModalProps {
  onClose: () => void
}

export default function NowReadingModal({ onClose }: NowReadingModalProps) {
  const activeReading = useReader((state) => state.activeReading)
  const narrationSentences = useReader((state) => state.narrationSentences)
  const tts = useReader((state) => state.tts)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const activeSentenceRef = useRef<HTMLButtonElement>(null)
  const previousFocusedElementRef = useRef<HTMLElement | null>(null)

  const isPreparing = tts.status === "preparing"
  const isPlaying = tts.status === "playing"
  const currentPage = tts.page ?? 1
  const currentSentenceIndex = tts.sentenceIndex ?? 0
  const [paragraphStarts, setParagraphStarts] = useState<number[]>([])
  const displayTitle = activeReading?.title || (activeReading ? `Document ${activeReading.docId}` : "Now Reading")
  const totalPages = activeReading?.numPages || 1

  useEffect(() => {
    let active = true
    setParagraphStarts([])
    if (!activeReading) return
    void getPage(activeReading.docId, currentPage).then((page) => {
      if (!active) return
      const content = page.content || ""
      const sentences = buildPageSentences(content, page.narration_exclusions)
      setParagraphStarts(sentences.flatMap((sentence, index) => {
        const previous = sentences[index - 1]
        return previous && /\n\s*\n/.test(content.slice(previous.end, sentence.start)) ? [index] : []
      }))
    }).catch(() => {})
    return () => { active = false }
  }, [activeReading, currentPage])

  useEffect(() => {
    previousFocusedElementRef.current = document.activeElement as HTMLElement | null
    containerRef.current?.focus()
    return () => {
      previousFocusedElementRef.current?.focus?.()
    }
  }, [])

  useEffect(() => {
    if (activeSentenceRef.current) {
      activeSentenceRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      })
    }
  }, [currentPage, currentSentenceIndex, narrationSentences])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const isInteractiveTarget =
        event.target instanceof HTMLElement &&
        ["BUTTON", "A", "INPUT", "TEXTAREA"].includes(event.target.tagName)

      if (event.key === "Tab" && containerRef.current) {
        const focusable = containerRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex='-1'])"
        )
        if (focusable.length > 0) {
          const firstElement = focusable[0]
          const lastElement = focusable[focusable.length - 1]
          if (event.shiftKey && document.activeElement === firstElement) {
            event.preventDefault()
            lastElement.focus()
            return
          }
          if (!event.shiftKey && document.activeElement === lastElement) {
            event.preventDefault()
            firstElement.focus()
            return
          }
        }
      }

      if (["Escape", " ", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
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
        if (isInteractiveTarget) {
          return
        }
        event.preventDefault()
        ttsEngine.toggle()
      } else if (event.key === "ArrowLeft") {
        event.preventDefault()
        ttsEngine.skipPage(-1)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        ttsEngine.skipPage(1)
      } else if (event.key === "ArrowUp") {
        event.preventDefault()
        if (currentSentenceIndex > 0) {
          ttsEngine.seekToSentence(currentSentenceIndex - 1)
        }
      } else if (event.key === "ArrowDown") {
        event.preventDefault()
        if (narrationSentences.length > 0 && currentSentenceIndex < narrationSentences.length - 1) {
          ttsEngine.seekToSentence(currentSentenceIndex + 1)
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown, true)
    return () => window.removeEventListener("keydown", handleKeyDown, true)
  }, [isFullscreen, onClose, currentSentenceIndex, narrationSentences.length])

  if (!activeReading) return null

  const handleOpenInBook = () => {
    const targetPage = tts.page ?? 1
    const { selectedDocId, selectDoc, setPage } = useReader.getState()
    if (selectedDocId !== activeReading.docId) {
      selectDoc(activeReading.docId, activeReading.numPages, targetPage)
    } else {
      setPage(targetPage)
    }
    onClose()
  }

  return (
    <div
      ref={containerRef}
      className={`podcast-modal-overlay now-reading-overlay ${isFullscreen ? "is-fullscreen-mode" : ""}`}
      data-testid="now-reading-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`Now reading: ${displayTitle}`}
      tabIndex={-1}
    >
      <div
        className={`now-reading-modal-container ${isFullscreen ? "is-fullscreen-container" : ""}`}
      >
        <div className="now-reading-grab-bar-area" onClick={onClose} aria-hidden="true">
          <div className="now-reading-grab-bar" />
        </div>

        <header className="now-reading-header">
          <div className="now-reading-top-bar">
            <div className="now-reading-badge">
              <BookOpen size={14} className="text-purple-400" />
              <span>Narration</span>
            </div>

            <div className="now-reading-header-actions">
              <button
                type="button"
                className="now-reading-icon-action-btn"
                onClick={() => setIsFullscreen((prev) => !prev)}
                aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
                title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
                data-testid="now-reading-fullscreen-toggle"
              >
                {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
              <button
                type="button"
                className="now-reading-icon-action-btn"
                onClick={onClose}
                aria-label="Close now reading view"
                title="Close"
                data-testid="now-reading-close"
              >
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="now-reading-title-section">
            <h2 className="now-reading-title" title={displayTitle} data-testid="now-reading-title">
              {displayTitle}
            </h2>
            <div className="now-reading-subbar">
              <span className="now-reading-page-chip" data-testid="now-reading-page">
                Page {currentPage} of {totalPages}
              </span>
              <Link
                href={`/dashboard/read/${activeReading.docId}`}
                className="now-reading-open-book-pill"
                onClick={handleOpenInBook}
                title="Open current page in book reader"
                data-testid="now-reading-open-book"
              >
                <BookOpen size={13} />
                <span>Open in book (Page {currentPage})</span>
                <ExternalLink size={12} className="opacity-70" />
              </Link>
            </div>
          </div>
        </header>

        <div
          className="now-reading-sentences-container"
          data-testid="now-reading-sentences"
          tabIndex={0}
          role="region"
          aria-label="Current page sentences"
        >
          {narrationSentences.length === 0 ? (
            <div className="now-reading-empty-sentences">
              {isPreparing ? (
                <div className="now-reading-status-card">
                  <Loader2 size={20} className="animate-spin text-purple-400" />
                  <p className="now-reading-status-title">Preparing audio for page {currentPage}</p>
                  <p className="now-reading-status-sub">Synthesizing natural voice narration...</p>
                </div>
              ) : (
                <div className="now-reading-status-card">
                  <BookOpen size={24} className="text-zinc-500 mb-1" />
                  <p className="now-reading-status-title">No parsed text on page {currentPage}</p>
                  <p className="now-reading-status-sub">Try skipping to another page using controls below</p>
                </div>
              )}
            </div>
          ) : (
            <div className="now-reading-sentences-list">
              {(() => {
                const sections: { id: number; items: { text: string; index: number }[] }[] = []
                let currentSection: { text: string; index: number }[] = []

                narrationSentences.forEach((sentenceText, index) => {
                  if (paragraphStarts.includes(index) && currentSection.length > 0) {
                    sections.push({ id: sections.length, items: currentSection })
                    currentSection = []
                  }
                  currentSection.push({ text: sentenceText, index })
                })
                if (currentSection.length > 0) {
                  sections.push({ id: sections.length, items: currentSection })
                }

                return sections.map((section) => (
                  <div key={section.id} className="now-reading-paragraph-section">
                    {section.items.map(({ text: sentenceText, index }) => {
                      const isCurrent = currentSentenceIndex === index
                      return (
                        <button
                          type="button"
                          key={index}
                          ref={isCurrent ? activeSentenceRef : null}
                          className={`now-reading-sentence ${isCurrent ? "is-active tts-sentence-active" : ""}`}
                          data-testid={`sentence-item-${index}`}
                          data-active={isCurrent ? "true" : undefined}
                          onClick={() => ttsEngine.seekToSentence(index)}
                          aria-current={isCurrent ? "true" : undefined}
                        >
                          <span className="now-reading-sentence-index">
                            {(index + 1).toString().padStart(2, "0")}
                          </span>
                          <span className="now-reading-sentence-text">{sentenceText}</span>
                          {isCurrent && (
                            <span data-testid="sentence-item-active" className="sr-only">
                              Current sentence
                            </span>
                          )}
                        </button>
                      )
                    })}
                  </div>
                ))
              })()}
            </div>
          )}
        </div>

        <footer className="now-reading-footer">
          <div className="now-reading-thumb-deck">
            <div className="now-reading-deck-side">
              <button
                type="button"
                className="now-reading-deck-btn"
                disabled={isPreparing || currentPage <= 1}
                onClick={() => ttsEngine.skipPage(-1)}
                aria-label="Previous page"
                title="Previous page"
                data-testid="now-reading-prev-page-btn"
              >
                <RotateCcw size={18} />
                <span className="now-reading-btn-sub">Prev</span>
              </button>

              <button
                type="button"
                className="now-reading-deck-btn now-reading-speed-chip"
                disabled={isPreparing}
                onClick={() => {
                  const speedOptions = [0.75, 1, 1.25, 1.5, 2]
                  const nextIndex = (speedOptions.indexOf(tts.speed) + 1) % speedOptions.length
                  ttsEngine.setSpeed(speedOptions[nextIndex])
                }}
                aria-label={`Playback speed ${tts.speed}x`}
                title="Change speed"
                data-testid="now-reading-speed-btn"
              >
                <span className="now-reading-speed-val">{tts.speed}x</span>
                <span className="now-reading-btn-sub">Speed</span>
              </button>
            </div>

            <div className="now-reading-deck-center">
              <button
                type="button"
                className="now-reading-hero-play"
                disabled={isPreparing}
                onClick={() => ttsEngine.toggle()}
                aria-label={isPlaying ? "Pause" : "Play"}
                data-testid="now-reading-play-btn"
              >
                {isPreparing ? (
                  <Loader2 size={24} className="animate-spin" />
                ) : isPlaying ? (
                  <Pause size={24} />
                ) : (
                  <Play size={24} style={{ marginLeft: "3px" }} />
                )}
              </button>
            </div>

            <div className="now-reading-deck-side">
              <button
                type="button"
                className="now-reading-deck-btn"
                disabled={
                  isPreparing ||
                  (activeReading.numPages > 0 && currentPage >= activeReading.numPages)
                }
                onClick={() => ttsEngine.skipPage(1)}
                aria-label="Next page"
                title="Next page"
                data-testid="now-reading-next-page-btn"
              >
                <RotateCw size={18} />
                <span className="now-reading-btn-sub">Next</span>
              </button>

              <button
                type="button"
                className="now-reading-deck-btn"
                onClick={() => ttsEngine.toggleMute()}
                aria-label={tts.muted ? "Unmute audio" : "Mute audio"}
                title={tts.muted ? "Unmute" : "Mute"}
                data-testid="now-reading-mute-btn"
              >
                {tts.muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
                <span className="now-reading-btn-sub">{tts.muted ? "Unmute" : "Mute"}</span>
              </button>

              <button
                type="button"
                className="now-reading-deck-btn now-reading-stop-action"
                onClick={() => ttsEngine.stop()}
                aria-label="Stop reading"
                title="Stop reading"
                data-testid="now-reading-stop-btn"
              >
                <Square size={16} />
                <span className="now-reading-btn-sub">Stop</span>
              </button>
            </div>
          </div>
        </footer>
      </div>
    </div>
  )
}
