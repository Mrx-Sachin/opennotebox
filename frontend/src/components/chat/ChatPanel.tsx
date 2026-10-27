"use client"

import { useEffect, useRef, useState } from "react"
import { Message } from "@/services/types"
import {
  createConversation,
  getMessages,
  getConversations,
  deleteConversation,
  getSettings,
  getLlmHealth,
  updateSettings,
  Settings,
  LlmBackendHealth,
  LlmBackend,
  RagMode,
} from "@/services/api"
import { streamMessage } from "@/services/chatStream"
import { useReader } from "@/stores/reader"
import { Send, Trash2, Sparkles, FileText, ChevronsUpDown, Cpu, BookOpen, Globe, Network, Search, XCircle, X, Loader2, AlertTriangle } from "lucide-react"
import { getKgStatus, buildKg, KgStatus } from "@/services/kgApi"
import CitationPopup from "@/components/chat/CitationPopup"
import ModelPicker from "@/components/chat/ModelPicker"
import MessageBody from "@/components/chat/MessageBody"

interface ChatPanelProps {
  docId: number
}


export default function ChatPanel({ docId }: ChatPanelProps) {
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [citationMsg, setCitationMsg] = useState<Message | null>(null)
  const jumpToCitation = useReader((s) => s.jumpToCitation)
  const listRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  // Which document the panel is currently showing. Read inside async send
  // handlers to detect that the user switched away mid-flight.
  const docIdRef = useRef(docId)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [switching, setSwitching] = useState(false)
  // The model picker's PUT failing used to be invisible (console only) -
  // the button quietly reverted to the old backend and nothing told the
  // user their switch didn't take.
  const [switchError, setSwitchError] = useState<string | null>(null)
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

  /** Switch the active backend, optionally changing which model it runs.
   *
   * A CLI bridge exposes several models behind one backend entry, so picking
   * "Claude Code · haiku" has to say which model as well as which backend.
   * That goes through `model_overrides`, which is per-user: writing the
   * backend's own `model` instead would push this choice onto every other
   * user of the deployment (see HANDOFF_LONGTERM.md 4a). */
  const selectModel = async (backend: LlmBackend, model?: string) => {
    if (!settings) return
    const current = backend.effective_model || backend.model
    const modelChanged = !!model && model !== current
    if (backend.name === settings.llm.active && !modelChanged) {
      setPickerOpen(false)
      return
    }
    const prev = settings
    const backends = settings.llm.backends.map((b) =>
      b.name === backend.name && modelChanged ? { ...b, effective_model: model! } : b,
    )
    setSettings({ ...settings, llm: { backends, active: backend.name } })
    setSwitching(true)
    setSwitchError(null)
    setPickerOpen(false)
    try {
      await updateSettings({
        llm: {
          active: backend.name,
          ...(modelChanged ? { model_overrides: { [backend.name]: model! } } : {}),
        },
      })
    } catch (err) {
      console.error("Failed to switch model:", err)
      setSettings(prev)
      setSwitchError(`Couldn't switch to ${backend.name} - the previous model is still active.`)
    } finally {
      setSwitching(false)
    }
  }

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

  const handleSend = async () => {
    if (!input.trim() || sending) return
    const question = input
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
      setInput("")
      const aiMessage = await streamMessage(
        activeConvId,
        question,
        useDoc ? [docId] : [],
        useDoc ? ragMode : undefined,
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
        return prev.map((msg) => msg.id === assistantDraftId ? aiMessage : msg)
      })
    } catch (err) {
      // A user-initiated Stop leaves the persisted user message and discards
      // the assistant draft, which the server saves only after completion.
      if (controller.signal.aborted) {
        setMessages((prev) => prev.filter((msg) => msg.id !== assistantDraftId))
        return
      }
      if (docIdRef.current !== sendDocId) return
      console.error("Failed to send message:", err)
      const detail = err instanceof Error ? err.message : undefined
      const errorContent = detail || "Something went wrong sending that message. Please try again."
      setMessages((prev) => {
        const existing = prev.find((msg) => msg.id === assistantDraftId)
        if (existing) {
          return prev.map((msg) =>
            msg.id === assistantDraftId
              ? { ...msg, content: `${msg.content}\n\n${errorContent}` }
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
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Sparkles className="h-5 w-5 text-indigo" />
            <p className="text-xs text-cream-dim">
              {useDoc ? "Ask anything about this document" : "Ask anything"}
            </p>
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
                      onJump={(c) => jumpToCitation(c.page, c.excerpt)}
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
          {/* Scope. A two-segment control, not a single button that swaps its
              own label - with a toggle you cannot tell whether the label is
              the current state or the action it would perform. */}
          <div
            role="group"
            aria-label="Answer scope"
            className="flex rounded-lg border border-surface bg-void p-0.5"
          >
            <button
              type="button"
              onClick={() => setUseDoc(true)}
              aria-pressed={useDoc}
              title="Ground answers in this document, with citations"
              className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                useDoc ? "bg-surface text-cream" : "text-cream-dim hover:text-cream"
              }`}
            >
              <BookOpen className="h-3 w-3 shrink-0" />
              Document
            </button>
            <button
              type="button"
              onClick={() => setUseDoc(false)}
              aria-pressed={!useDoc}
              title="Answer from general knowledge, without reading this document"
              className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                !useDoc ? "bg-surface text-cream" : "text-cream-dim hover:text-cream"
              }`}
            >
              <Globe className="h-3 w-3 shrink-0" />
              General
            </button>
          </div>

          {/* RAG mode. Only shown when document grounding is active -
              general questions have no retrieval to configure. */}
          {useDoc && (
            <div
              role="group"
              aria-label="Retrieval mode"
              className="flex rounded-lg border border-surface bg-void p-0.5"
            >
              <button
                type="button"
                onClick={() => setRagMode("standard")}
                aria-pressed={ragMode === "standard"}
                title="BM25 + embeddings + LLM reranking"
                className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                  ragMode === "standard" ? "bg-surface text-cream" : "text-cream-dim hover:text-cream"
                }`}
              >
                <Search className="h-3 w-3 shrink-0" />
                Standard
              </button>
              <button
                type="button"
                onClick={() => setRagMode("hybrid")}
                aria-pressed={ragMode === "hybrid"}
                title="Vector retrieval + knowledge graph"
                className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                  ragMode === "hybrid" ? "bg-surface text-cream" : "text-cream-dim hover:text-cream"
                }`}
              >
                <Network className="h-3 w-3 shrink-0" />
                Hybrid
              </button>
              <button
                type="button"
                onClick={() => setRagMode("graph")}
                aria-pressed={ragMode === "graph"}
                title="Knowledge graph only"
                className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                  ragMode === "graph" ? "bg-surface text-cream" : "text-cream-dim hover:text-cream"
                }`}
              >
                <Sparkles className="h-3 w-3 shrink-0" />
                Graph
              </button>
            </div>
          )}

          {/* KG readiness, surfaced before a question can dead-end. Graph
              mode has nothing to search until the KG exists; Hybrid runs
              fine but loses its graph half. Both say so here, with a
              build action, instead of failing after the ask. */}
          {needsKg && kgStatus && kgStatus.status !== "completed" && (
            <div className="flex items-start gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-snug text-amber-300">
              {kgStatus.status === "extracting" ? (
                <Loader2 className="mt-px h-3 w-3 shrink-0 animate-spin" />
              ) : (
                <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
              )}
              <span className="min-w-0 flex-1">
                {kgStatus.status === "extracting"
                  ? `Building the knowledge graph (${kgStatus.chunks_processed}/${kgStatus.total_chunks || "…"} chunks)…`
                  : ragMode === "graph"
                    ? "No knowledge graph yet - Graph mode has nothing to search until it is built."
                    : "No knowledge graph yet - Hybrid will answer from the full text only until it is built."}
              </span>
              {kgStatus.status !== "extracting" && (
                <button
                  type="button"
                  onClick={handleBuildKg}
                  disabled={kgBuilding}
                  className="shrink-0 rounded-md border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-200 transition-colors hover:bg-amber-500/20 disabled:opacity-40"
                >
                  {kgBuilding ? "Starting…" : "Build now"}
                </button>
              )}
            </div>
          )}

          {settings && (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              aria-haspopup="dialog"
              title={`${activeBackend?.effective_model || activeBackend?.model || settings.llm.active} · ${settings.llm.active}`}
              className={`flex w-full items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-medium transition-colors ${
                activeHealth?.status === "unhealthy"
                  ? "border-[#ef4444]/50 text-[#f87171] hover:border-[#ef4444]/70"
                  : "border-surface text-cream-dim hover:border-indigo/40 hover:text-cream"
              }`}
            >
              <Cpu className={`h-3 w-3 shrink-0 ${activeHealth?.status === "unhealthy" ? "text-[#f87171]" : "text-indigo"}`} />
              <span className="min-w-0 flex-1 truncate text-left">
                {switching
                  ? "Switching…"
                  : activeBackend?.effective_model ||
                    activeBackend?.model ||
                    settings.llm.active}
              </span>
              <ChevronsUpDown className="h-3 w-3 shrink-0" />
            </button>
          )}
          {activeHealth?.status === "unhealthy" && (
            <p className="flex items-start gap-1.5 rounded-md border border-[#ef4444]/30 bg-[#ef4444]/10 px-2 py-1.5 text-[11px] leading-snug text-[#f87171]">
              <XCircle className="mt-px h-3 w-3 shrink-0" />
              <span>
                {settings?.llm.active} is unreachable from the server - messages will not be
                answered until the bridge is running.
              </span>
            </p>
          )}
          {switchError && (
            <p className="flex items-start gap-1.5 rounded-md border border-[#ef4444]/30 bg-[#ef4444]/10 px-2 py-1.5 text-[11px] leading-snug text-[#f87171]">
              <XCircle className="mt-px h-3 w-3 shrink-0" />
              <span className="min-w-0 flex-1">{switchError}</span>
              <button
                type="button"
                onClick={() => setSwitchError(null)}
                aria-label="Dismiss"
                className="shrink-0 rounded p-0.5 transition-colors hover:bg-[#ef4444]/20"
              >
                <X className="h-3 w-3" />
              </button>
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
            placeholder={useDoc ? "Ask about this document…" : "Ask anything…"}
            disabled={sending}
            className="min-w-0 flex-1 rounded-lg border border-surface bg-void px-3 py-2 text-[13px] text-cream placeholder:text-cream-dim focus:border-indigo/60 focus:outline-none"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending}
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

      {settings && (
        <ModelPicker
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          backends={settings.llm.backends}
          activeName={settings.llm.active}
          onSelect={selectModel}
        />
      )}

      {citationMsg && (
        <CitationPopup
          citations={citationMsg.citations || []}
          onClose={() => setCitationMsg(null)}
          onJump={(citation) => jumpToCitation(citation.page, citation.excerpt)}
        />
      )}
    </div>
  )
}
