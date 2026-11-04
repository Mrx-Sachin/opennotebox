"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Citation, Message } from "@/services/types"
import {
  createConversation,
  getMessages,
  getConversations,
  deleteConversation,
  getSettings,
  getLlmHealth,
  Settings,
  LlmBackendHealth,
  RagMode,
} from "@/services/api"
import { streamMessage } from "@/services/chatStream"
import { useReader } from "@/stores/reader"
import { Send, Trash2, Sparkles, FileText, BookOpen, Globe, Network, Search, XCircle, Loader2, AlertTriangle, Mic, Volume2, Square, Copy, Check } from "lucide-react"
import { startRecording, ActiveRecording } from "@/lib/audioRecorder"
import { transcribeChatAudio } from "@/services/api"
import { ttsEngine } from "@/services/ttsEngine"
import { getKgStatus, buildKg, KgStatus } from "@/services/kgApi"
import CitationPopup from "@/components/chat/CitationPopup"
import MessageBody from "@/components/chat/MessageBody"

interface ChatPanelProps {
  docId: number
}


const PROMPT_STARTERS = [
  "Summarize key ideas",
  "Explain main conclusions",
  "What are the core concepts?",
]

export default function ChatPanel({ docId }: ChatPanelProps) {
  const router = useRouter()
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const activeRecorderRef = useRef<ActiveRecording | null>(null)
  const [speakingMsgId, setSpeakingMsgId] = useState<number | string | null>(null)
  const [copiedMsgId, setCopiedMsgId] = useState<number | string | null>(null)
  const ttsStatus = useReader((s) => s.tts.status)

  // Reset active speaking message when audio engine stops
  useEffect(() => {
    if (ttsStatus === "idle") {
      setSpeakingMsgId(null)
    }
  }, [ttsStatus])
  const [citationMsg, setCitationMsg] = useState<Message | null>(null)
  const jumpToCitation = useReader((s) => s.jumpToCitation)
  const handleJump = (citation: Citation) => {
    if (citation.doc_id != null && citation.doc_id !== docId) {
      router.push(`/dashboard/read/${citation.doc_id}`)
    }
    jumpToCitation(citation)
  }
  const listRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  // Which document the panel is currently showing. Read inside async send
  // handlers to detect that the user switched away mid-flight.
  const docIdRef = useRef(docId)
  const [settings, setSettings] = useState<Settings | null>(null)
  // Live reachability of the active backend, from the API-side probe. This
  // is what tells the user "your backend is down" BEFORE a chat request
  // fails with a 503 - the stored settings row alone can't.
  const [activeHealth, setActiveHealth] = useState<LlmBackendHealth | null>(null)
  // When off, the question is sent with no document attached, so the backend
  // answers it as a general question instead of grounding it in this PDF.
  const [useDoc, setUseDoc] = useState(true)
  const [ragMode, setRagMode] = useState<RagMode>("standard")
  // KG readiness for the current document. Graph mode has nothing to
  // search until the KG is built, and Hybrid silently loses its graph
  // half - both are worth flagging in the composer BEFORE a question
  // dead-ends, not after.
  const [kgStatus, setKgStatus] = useState<KgStatus | null>(null)
  const [kgBuilding, setKgBuilding] = useState(false)
  const activeBackend = settings?.llm.backends.find((b) => b.name === settings.llm.active)

  const currentModelName =
    activeBackend?.effective_model || activeBackend?.model || settings?.llm.active || "muse-spark-1.3-contributor"

  const displayModelName = (() => {
    const full =
      activeBackend?.effective_model || activeBackend?.model || settings?.llm.active
    if (!full) return "Muse Contributor"
    if (full === "muse-spark-1.3-contributor") return "Muse Contributor"
    const parts = full.split("/")
    return parts[parts.length - 1]
  })()

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((err) => console.error("Failed to load model settings:", err))
  }, [])

  // Refresh the active backend's health when it changes, then keep
  // re-checking so a bridge that comes back is reflected without a reload.
  useEffect(() => {
    const activeName = settings?.llm.active
    if (!activeName) return
    let cancelled = false
    const check = () =>
      getLlmHealth()
        .then((h) => {
          if (cancelled) return
          const mine = h.backends.find((b) => b.name === activeName)
          setActiveHealth(mine || null)
        })
        .catch(() => {
          /* health check failing is not itself an error worth surfacing */
        })
    check()
    const interval = setInterval(check, 8000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [settings?.llm.active])

  // Switching documents swaps the whole conversation out from under any
  // request that is still in flight. Without this, the old request kept
  // running against the old document, the composer stayed stuck showing
  // "Stop" over the new document's history, and its answer eventually
  // landed in the wrong conversation.
  useEffect(() => {
    docIdRef.current = docId
    let stale = false
    abortRef.current?.abort()
    abortRef.current = null
    setSending(false)
    setConversationId(null)
    setMessages([])

    const initChat = async () => {
      try {
        const convs = await getConversations()
        const existing = convs.find((c) => c.document_ids?.includes(docId))
        if (stale) return
        if (existing) {
          const msgs = await getMessages(existing.id)
          if (stale) return
          setConversationId(existing.id)
          setMessages(msgs)
        }
      } catch (err) {
        if (!stale) console.error("Failed to load chat history:", err)
      }
    }
    initChat()
    return () => {
      stale = true
    }
  }, [docId])

  // Abort anything still running when the panel itself goes away.
  useEffect(() => () => abortRef.current?.abort(), [])

  // Track KG readiness while a graph-dependent mode is selected. Polls only
  // in that window (standard mode has no KG prerequisite), so the notice
  // flips off on its own after a build - whether it was started here or from
  // the KG tab - instead of waiting for the next question to dead-end.
  const needsKg = useDoc && (ragMode === "hybrid" || ragMode === "graph")
  useEffect(() => {
    setKgStatus(null)
    if (!needsKg) return
    let cancelled = false
    const load = () =>
      getKgStatus(docId)
        .then((s) => {
          if (!cancelled) setKgStatus(s)
        })
        .catch(() => {
          /* advisory probe; a failed status check is not worth surfacing */
        })
    load()
    const interval = setInterval(load, 3000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [docId, needsKg])

  const handleBuildKg = async () => {
    const id = docIdRef.current
    setKgBuilding(true)
    try {
      await buildKg(id)
      if (docIdRef.current === id) {
        getKgStatus(id).then(setKgStatus).catch(() => {})
      }
    } catch {
      /* the 3s poll picks up the real state either way */
    } finally {
      if (docIdRef.current === id) setKgBuilding(false)
    }
  }

  // Direct scrollTop on the chat's own list, not scrollIntoView(). By spec,
  // scrollIntoView() scrolls EVERY scrollable ancestor needed to bring the
  // target into view, not just the nearest one - if any outer layout
  // container is ever eligible to scroll (even fleetingly, e.g. during a
  // reflow while a message streams in), it scrolls that too, which is
  // exactly what dropped the whole page below the fixed reader layout on
  // send. Setting scrollTop directly on the chat's own container can only
  // ever affect that one element.
  const lastMessageContent = messages[messages.length - 1]?.content
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [lastMessageContent, messages.length, sending])

  const handleClear = async () => {
    if (conversationId) {
      try {
        await deleteConversation(conversationId)
      } catch (err) {
        console.error("Failed to delete conversation:", err)
      }
    }
    setConversationId(null)
    setMessages([])
  }

  // Clean up recording on unmount
  useEffect(() => {
    return () => {
      if (activeRecorderRef.current) {
        void activeRecorderRef.current.cancel()
        activeRecorderRef.current = null
      }
    }
  }, [])

  // Recording timer
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null
    if (isRecording) {
      interval = setInterval(() => {
        setRecordingSeconds((prev) => prev + 1)
      }, 1000)
    } else {
      setRecordingSeconds(0)
    }
    return () => {
      if (interval) clearInterval(interval)
    }
  }, [isRecording])

  const handleStartRecording = async () => {
    try {
      const rec = await startRecording()
      activeRecorderRef.current = rec
      setIsRecording(true)
    } catch (err) {
      console.error("Failed to start mic recording:", err)
      alert("Microphone access was denied or is not supported.")
    }
  }

  const handleStopRecording = async () => {
    if (!activeRecorderRef.current) return
    setIsRecording(false)
    setIsTranscribing(true)
    try {
      const wavBlob = await activeRecorderRef.current.stop()
      activeRecorderRef.current = null
      const res = await transcribeChatAudio(wavBlob)
      if (res.transcript && res.transcript.trim()) {
        setInput(res.transcript.trim())
        void handleSend(res.transcript.trim())
      }
    } catch (err) {
      console.error("Voice transcription failed:", err)
      alert("Voice transcription failed. You can still type your question.")
    } finally {
      setIsTranscribing(false)
      activeRecorderRef.current = null
    }
  }

  const handleToggleSpeak = async (msg: Message) => {
    if (speakingMsgId === msg.id && (ttsStatus === "playing" || ttsStatus === "preparing")) {
      ttsEngine.stopSpeaking()
      setSpeakingMsgId(null)
    } else {
      setSpeakingMsgId(msg.id)
      try {
        await ttsEngine.speakText(msg.content, msg.id)
      } catch (err) {
        console.error("Failed to read message out loud:", err)
        setSpeakingMsgId(null)
      }
    }
  }

  const handleCopy = async (msg: Message) => {
    const cleanContent = msg.content
      .replace(/\[\d+(?:\s*,\s*\d+)*\]/g, "")
      .trim()
    try {
      await navigator.clipboard.writeText(cleanContent)
      setCopiedMsgId(msg.id)
      setTimeout(() => {
        setCopiedMsgId((curr) => (curr === msg.id ? null : curr))
      }, 2000)
    } catch (err) {
      console.error("Failed to copy message:", err)
    }
  }

  const handleSend = async (questionOverride?: string) => {
    const question = (questionOverride !== undefined ? questionOverride : input).trim()
    if (!question || sending) return
    const sendDocId = docId
    setSending(true)
    const controller = new AbortController()
    abortRef.current = controller
    const assistantDraftId = -Date.now()
    let assistantDraftAdded = false
    let activeConvId = conversationId ?? 0
    try {
      if (!activeConvId) {
        const conv = await createConversation()
        activeConvId = conv.id
        setConversationId(activeConvId)
      }
      const userMessage: Message = {
        id: Date.now(),
        conversation_id: activeConvId,
        role: "user",
        content: question,
        token_count: 0,
      }
      setMessages((prev) => [...prev, userMessage])
      if (questionOverride === undefined) setInput("")
      const effectiveRagMode: RagMode =
        useDoc && (kgStatus?.status === "completed" || (kgStatus?.entity_count ?? 0) > 0)
          ? "hybrid"
          : "standard"

      const aiMessage = await streamMessage(
        activeConvId,
        question,
        useDoc ? [docId] : [],
        useDoc ? effectiveRagMode : undefined,
        controller.signal,
        {
          onToken: (content) => {
            if (docIdRef.current !== sendDocId) return
            assistantDraftAdded = true
            setMessages((prev) => {
              const existing = prev.find((msg) => msg.id === assistantDraftId)
              if (existing) {
                return prev.map((msg) =>
                  msg.id === assistantDraftId
                    ? { ...msg, content: msg.content + content }
                    : msg,
                )
              }
              return [
                ...prev,
                {
                  id: assistantDraftId,
                  conversation_id: activeConvId,
                  role: "assistant",
                  content,
                  token_count: 0,
                },
              ]
            })
          },
        },
      )
      // The response can arrive in the gap between the user switching
      // documents and the abort landing - dropping it here keeps one
      // document's answer out of another document's transcript.
      if (docIdRef.current !== sendDocId) return
      setMessages((prev) => {
        if (!assistantDraftAdded) return [...prev, aiMessage]
        return prev.map((msg) => (msg.id === assistantDraftId ? aiMessage : msg))
      })
    } catch (err: unknown) {
      // A user-initiated Stop leaves the persisted user message and discards
      // the assistant draft, which the server saves only after completion.
      if (controller.signal.aborted) {
        setMessages((prev) => prev.filter((msg) => msg.id !== assistantDraftId))
        return
      }
      if (docIdRef.current !== sendDocId) return
      console.error("Failed to send message:", err)
      let errorContent =
        "Sorry, I ran into an error while generating a response. Please try again."
      if (err && typeof err === "object" && "response" in err) {
        const responseData = (err as { response?: { data?: { detail?: string } } }).response?.data
        if (responseData?.detail) {
          errorContent = responseData.detail
        }
      } else if (err instanceof Error && err.message) {
        errorContent = err.message
      }
      setMessages((prev) => {
        const existing = prev.find((msg) => msg.id === assistantDraftId)
        if (existing) {
          return prev.map((msg) =>
            msg.id === assistantDraftId
              ? { ...msg, content: errorContent }
              : msg,
          )
        }
        return [
          ...prev,
          {
            id: assistantDraftId,
            conversation_id: activeConvId,
            role: "assistant",
            content: errorContent,
            token_count: 0,
          },
        ]
      })
    } finally {
      // Only clear state we still own - if the document changed mid-flight,
      // the effect above has already reset both for the new document.
      if (docIdRef.current === sendDocId) {
        abortRef.current = null
        setSending(false)
      }
    }
  }

  /** Abort the in-flight stream, which cancels the model coroutine server-side
   * instead of only hiding the "thinking" indicator. */
  const handleStop = () => {
    abortRef.current?.abort()
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={listRef} className="reader-scroll flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3.5 p-4 text-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-indigo/20 bg-indigo/10 shadow-glow">
              <Sparkles className="h-5 w-5 text-indigo" />
            </div>
            <div className="space-y-1">
              <h4 className="font-display text-sm font-semibold text-cream">
                {useDoc ? "Document Assistant" : "General Assistant"}
              </h4>
              <p className="max-w-[240px] text-xs text-cream-dim leading-relaxed">
                {useDoc
                  ? "Ask questions, extract key takeaways, or explore concepts grounded in this document."
                  : "Ask general knowledge questions without document context."}
              </p>
            </div>
            {useDoc && (
              <div className="mt-2 flex w-full max-w-[260px] flex-col gap-2">
                {PROMPT_STARTERS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => handleSend(prompt)}
                    className="flex items-center justify-between rounded-lg border border-surface bg-surface/30 px-3 py-2 text-left text-xs text-cream-dim transition-[border-color,background-color,color] duration-150 hover:border-indigo/40 hover:bg-surface/70 hover:text-cream"
                  >
                    <span>{prompt}</span>
                    <Sparkles size={11} className="shrink-0 text-indigo/60" />
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div className="max-w-[85%]">
                <div
                  className={`rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                    msg.role === "user"
                      ? "gradient-primary text-white"
                      : // Not .glass here: --glass-bg is the panel colour at 60%
                        // opacity, so on the chat panel's own background an
                        // assistant bubble rendered as bare text with an
                        // invisible border. --surface actually reads as a
                        // surface sitting on top of the panel.
                        "border border-white/10 bg-surface/70 text-cream"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <MessageBody
                      content={msg.content}
                      citations={msg.citations}
                      onJump={handleJump}
                    />
                  ) : (
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  )}
                </div>
                {msg.citations && msg.citations.length > 0 && !/\[\d/.test(msg.content) && (
                  <button
                    onClick={() => setCitationMsg(msg)}
                    className="mt-1 flex items-center gap-1 text-[11px] text-indigo transition-colors hover:text-cream"
                  >
                    <FileText className="h-3 w-3" />
                    {msg.citations.length} source{msg.citations.length > 1 ? "s" : ""}
                  </button>
                )}
                {msg.role === "assistant" && (
                  <div className="mt-1.5 flex items-center gap-1 text-cream-dim">
                    <button
                      type="button"
                      onClick={() => handleToggleSpeak(msg)}
                      title={
                        speakingMsgId === msg.id && (ttsStatus === "playing" || ttsStatus === "preparing")
                          ? "Stop reading"
                          : "Read aloud"
                      }
                      className={`flex h-6 w-6 items-center justify-center rounded-md transition-colors ${
                        speakingMsgId === msg.id
                          ? "bg-indigo/20 text-indigo"
                          : "hover:bg-white/10 hover:text-cream"
                      }`}
                    >
                      {speakingMsgId === msg.id && ttsStatus === "playing" ? (
                        <Square className="h-3 w-3 fill-current text-indigo animate-pulse" />
                      ) : speakingMsgId === msg.id && ttsStatus === "preparing" ? (
                        <Loader2 className="h-3 w-3 animate-spin text-indigo" />
                      ) : (
                        <Volume2 className="h-3.5 w-3.5" />
                      )}
                    </button>

                    <button
                      type="button"
                      onClick={() => handleCopy(msg)}
                      title={copiedMsgId === msg.id ? "Copied!" : "Copy message"}
                      className="flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-white/10 hover:text-cream"
                    >
                      {copiedMsgId === msg.id ? (
                        <Check className="h-3.5 w-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        {sending && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-surface/70 px-3 py-2">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo [animation-delay:300ms]" />
              <button
                type="button"
                onClick={handleStop}
                className="ml-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-cream-dim transition-colors hover:bg-surface hover:text-[#ef4444]"
              >
                Stop
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-surface p-3">
        {/* Composer controls. Two stacked full-width rows rather than pills
            crammed onto one line: this panel is ~190px wide, so anything
            side-by-side truncates to nothing the moment a model id is long. */}
        <div className="mb-2 space-y-1.5">
          {/* Top Utility Micro-Bar: Scope on Left, Active Model Badge on Right */}
          <div className="flex items-center justify-between gap-2 px-0.5">
            <div
              role="group"
              aria-label="Answer scope"
              className="flex items-center rounded-lg border border-surface bg-void/80 p-0.5"
            >
              <button
                type="button"
                onClick={() => setUseDoc(true)}
                aria-pressed={useDoc}
                title="Ground answers in this document, with citations"
                className={`flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-[background-color,color] duration-150 ${
                  useDoc ? "bg-surface text-cream shadow-sm" : "text-cream-dim hover:text-cream"
                }`}
              >
                <BookOpen size={12} className={useDoc ? "text-indigo" : "text-cream-dim"} />
                <span>Document</span>
              </button>
              <button
                type="button"
                onClick={() => setUseDoc(false)}
                aria-pressed={!useDoc}
                title="Answer from general knowledge, without reading this document"
                className={`flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-[background-color,color] duration-150 ${
                  !useDoc ? "bg-surface text-cream shadow-sm" : "text-cream-dim hover:text-cream"
                }`}
              >
                <Globe size={12} className={!useDoc ? "text-indigo" : "text-cream-dim"} />
                <span>General</span>
              </button>
            </div>

            {/* Active Model Indicator Badge (Managed in Settings, Default Muse) */}
            {settings && (
              <div
                className="flex items-center gap-1.5 rounded-md border border-surface bg-void/80 px-2 py-1 text-[11px] text-cream-dim"
                title={`Active model: ${currentModelName} (managed in Settings)`}
              >
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    activeHealth?.status === "unhealthy"
                      ? "bg-[#ef4444]"
                      : "bg-emerald-400"
                  }`}
                />
                <Sparkles size={11} className="text-indigo shrink-0" />
                <span className="font-medium text-cream truncate max-w-[140px]">{displayModelName}</span>
              </div>
            )}
          </div>
          {activeHealth?.status === "unhealthy" && (
            <p className="flex items-start gap-1.5 rounded-md border border-[#ef4444]/30 bg-[#ef4444]/10 px-2 py-1.5 text-[11px] leading-snug text-[#f87171]">
              <XCircle className="mt-px h-3 w-3 shrink-0" />
              <span>
                {settings?.llm.active} is unreachable from the server - messages will not be
                answered until the bridge is running.
              </span>
            </p>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSend()
          }}
          className="flex items-center gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              isRecording
                ? `Recording speech (${recordingSeconds}s)...`
                : isTranscribing
                  ? "Transcribing with Meta Muse..."
                  : useDoc
                    ? "Ask about this document…"
                    : "Ask anything…"
            }
            disabled={sending || isRecording || isTranscribing}
            className={`min-w-0 flex-1 rounded-lg border bg-void px-3 py-2 text-[13px] text-cream placeholder:text-cream-dim focus:border-indigo/60 focus:outline-none ${
              isRecording ? "border-rose-500/80 bg-rose-500/10" : "border-surface"
            }`}
          />

          {isRecording ? (
            <button
              type="button"
              onClick={handleStopRecording}
              title="Stop recording and transcribe"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-rose-500 text-white shadow-lg animate-pulse hover:bg-rose-600 transition-colors"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStartRecording}
              disabled={sending || isTranscribing}
              title="Voice input via Meta Muse Voice Transcribe"
              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                isTranscribing
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                  : "border-surface text-cream-dim hover:border-indigo/40 hover:text-cream"
              }`}
            >
              {isTranscribing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Mic className="h-3.5 w-3.5" />
              )}
            </button>
          )}

          <button
            type="submit"
            disabled={!input.trim() || sending || isRecording || isTranscribing}
            title="Send"
            className="gradient-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white shadow-glow transition-transform hover:scale-105 disabled:opacity-40"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={handleClear}
              title="Clear chat"
              className="shrink-0 rounded-lg p-2 text-cream-dim transition-colors hover:text-[#ef4444]"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </form>
      </div>

      {citationMsg && (
        <CitationPopup
          citations={citationMsg.citations || []}
          onClose={() => setCitationMsg(null)}
          onJump={handleJump}
        />
      )}
    </div>
  )
}
