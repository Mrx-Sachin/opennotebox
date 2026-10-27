"use client"

import { useEffect, useRef, useState } from "react"
import {
  X,
  Check,
  Trash2,
  Plus,
  Loader2,
  AlertCircle,
  Play,
  Square,
  FolderOutput,
  Sparkles,
  Mic,
  Settings2,
  Terminal,
  Copy,
  CheckCircle2,
  RefreshCw,
  Shield,
  BookOpen,
} from "lucide-react"
import {
  Settings,
  LlmBackend,
  TtsVoice,
  getSettings,
  updateSettings,
  getTtsVoices,
  playText,
  getTtsCacheStatus,
  fetchAudioBlobUrl,
  getAudioUrl,
  exportNow,
  probeLlm,
} from "@/services/api"
import { copyText } from "@/lib/clipboard"
import { ttsEngine } from "@/services/ttsEngine"
import { useReader } from "@/stores/reader"
import { useAuth } from "@/stores/auth"

const SAMPLE_TEXT =
  "The quick brown fox jumps over the lazy dog, while the sun sets gently behind the hills."

interface SettingsModalProps {
  open: boolean
  onClose: () => void
}

const CATEGORIES = [
  { key: "ai", label: "AI Model", icon: Sparkles },
  { key: "voice", label: "Voice", icon: Mic },
  { key: "reader", label: "Reader", icon: BookOpen },
  { key: "export", label: "Export", icon: FolderOutput },
  { key: "admin", label: "Admin", icon: Shield },
] as const
type CategoryKey = (typeof CATEGORIES)[number]["key"]

// `tools/cli-bridge/launcher.py` runs on the user's own machine, auto-detects
// which of these CLIs are installed and logged in, and starts a bridge for
// each one on its own fixed port. The browser (also on the user's machine)
// pings 127.0.0.1:<port> directly to detect readiness; the backend, which is
// dockerized, talks to the same bridge over host.docker.internal instead.
const LAUNCHER_COMMAND = "python3 tools/cli-bridge/launcher.py"

// "cli-default" means "whatever this CLI is already configured to use". Do
// not put the tool's own name here: OpenAI-compatible clients branch on the
// model id, and langchain sends anything matching its codex heuristic to the
// Responses API, which the bridge does not implement - a backend whose model
// was literally "codex" 404'd on every message.
const CLI_DEFAULT_MODEL = "cli-default"
const CLI_PRESETS = [
  { id: "claude", label: "Claude Code", port: 8765 },
  { id: "codex", label: "Codex", port: 8766 },
  { id: "opencode", label: "OpenCode", port: 8767 },
  { id: "grok", label: "Grok", port: 8768 },
] as const
const LEGACY_PLACEHOLDER_MODELS = new Set<string>(["claude", "codex", "opencode", "grok"])
const CLI_PRESET_NAMES = new Set<string>(CLI_PRESETS.map((p) => p.label))

const ENDPOINT_PRESETS = [
  { id: "openai", label: "OpenAI", name: "OpenAI", base_url: "https://api.openai.com/v1" },
  { id: "groq", label: "Groq", name: "Groq", base_url: "https://api.groq.com/openai/v1" },
  { id: "openrouter", label: "OpenRouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
  { id: "ollama", label: "Ollama", name: "Ollama", base_url: "http://host.docker.internal:11434/v1" },
  { id: "custom", label: "Custom", name: "", base_url: "" },
] as const

export default function SettingsModal({ open, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [voices, setVoices] = useState<TtsVoice[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [saved, setSaved] = useState(false)
  // Every settings write below is optimistic; without this the rollback was
  // the only sign of failure, which reads as "the click did nothing".
  const [saveError, setSaveError] = useState<string | null>(null)
  const [activeCategory, setActiveCategory] = useState<CategoryKey>("ai")
  const [showAddForm, setShowAddForm] = useState(false)
  const [newBackend, setNewBackend] = useState({ name: "", base_url: "", model: "", api_key: "" })
  const [adding, setAdding] = useState(false)
  const [endpointPreset, setEndpointPreset] = useState<(typeof ENDPOINT_PRESETS)[number]["id"]>("custom")
  const [probedModels, setProbedModels] = useState<string[]>([])
  const [probingModels, setProbingModels] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  const isAdmin = useAuth((s) => s.user?.is_admin) === true
  const [connectingPreset, setConnectingPreset] = useState<string | null>(null)
  const [detected, setDetected] = useState<Record<string, boolean>>({})
  const [copiedLauncher, setCopiedLauncher] = useState(false)
  const [previewVoice, setPreviewVoice] = useState<string | null>(null)
  const [previewStatus, setPreviewStatus] = useState<"loading" | "playing" | null>(null)
  const previewAudioRef = useRef<HTMLAudioElement | null>(null)
  const previewBlobUrlRef = useRef<string | null>(null)
  const previewTokenRef = useRef(0)
  const copyOnSelect = useReader((s) => s.copyOnSelect)
  const setCopyOnSelect = useReader((s) => s.setCopyOnSelect)
  const [exportToggling, setExportToggling] = useState(false)
  const [exportRunning, setExportRunning] = useState(false)
  const [exportMessage, setExportMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setLoadError(false)
    setShowAddForm(false)
    Promise.all([getSettings(), getTtsVoices()])
      .then(([s, v]) => {
        setSettings(s)
        setVoices(v.voices)
        // Remember the saved voice for the engine; does not interrupt playback.
        ttsEngine.syncVoice(s.tts.default_voice)
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [open])

  useEffect(() => {
    if (!open || activeCategory !== "ai") return
    let cancelled = false
    const probe = () => {
      CLI_PRESETS.forEach((preset) => {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 1000)
        fetch(`http://127.0.0.1:${preset.port}/v1/models`, { signal: controller.signal })
          .then((res) => {
            if (!cancelled) setDetected((d) => ({ ...d, [preset.id]: res.ok }))
          })
          .catch(() => {
            if (!cancelled) setDetected((d) => ({ ...d, [preset.id]: false }))
          })
          .finally(() => clearTimeout(timer))
      })
    }
    probe()
    const interval = setInterval(probe, 4000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [open, activeCategory])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      e.stopPropagation()
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", handler, true)
    return () => window.removeEventListener("keydown", handler, true)
  }, [open, onClose])

  const stopPreview = () => {
    previewTokenRef.current++
    previewAudioRef.current?.pause()
    previewAudioRef.current = null
    if (previewBlobUrlRef.current) {
      URL.revokeObjectURL(previewBlobUrlRef.current)
      previewBlobUrlRef.current = null
    }
    setPreviewVoice(null)
    setPreviewStatus(null)
  }

  useEffect(() => {
    if (!open) stopPreview()
  }, [open])

  useEffect(() => stopPreview, [])

  const playPreview = async (voiceId: string) => {
    if (previewVoice === voiceId) {
      stopPreview()
      return
    }
    stopPreview()
    const token = ++previewTokenRef.current
    setPreviewVoice(voiceId)
    setPreviewStatus("loading")
    try {
      const res = await playText(SAMPLE_TEXT, voiceId)
      let cacheId = res.status === "cached" ? res.cache_id : undefined
      if (cacheId == null) {
        for (let i = 0; i < 150 && cacheId == null; i++) {
          if (token !== previewTokenRef.current) return
          await new Promise((r) => setTimeout(r, 400))
          const status = await getTtsCacheStatus(res.text_hash)
          if (status.status === "ready" && status.cache_id != null) cacheId = status.cache_id
        }
      }
      if (token !== previewTokenRef.current || cacheId == null) return
      const blobUrl = await fetchAudioBlobUrl(getAudioUrl(cacheId))
      if (token !== previewTokenRef.current) return
      previewBlobUrlRef.current = blobUrl
      const audio = new Audio(blobUrl)
      previewAudioRef.current = audio
      audio.onended = () => {
        if (token === previewTokenRef.current) stopPreview()
      }
      setPreviewStatus("playing")
      await audio.play()
    } catch {
      if (token === previewTokenRef.current) stopPreview()
    }
  }

  const flashSaved = () => {
    setSaveError(null)
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  const selectBackend = async (name: string) => {
    if (!settings) return
    const prev = settings
    setSettings({ ...settings, llm: { ...settings.llm, active: name } })
    try {
      await updateSettings({ llm: { active: name } })
      flashSaved()
    } catch {
      setSettings(prev)
      setSaveError("Couldn't switch the active model. Check the backend is running.")
    }
  }

  const deleteBackend = async (name: string) => {
    if (!settings) return
    const prev = settings
    const backends = settings.llm.backends.filter((b) => b.name !== name)
    setSettings({ ...settings, llm: { ...settings.llm, backends } })
    try {
      await updateSettings({ llm: { backends } })
      flashSaved()
    } catch {
      setSettings(prev)
      setSaveError("Couldn't remove that backend.")
    }
  }

  const resetAddForm = () => {
    setNewBackend({ name: "", base_url: "", model: "", api_key: "" })
    setEndpointPreset("custom")
    setProbedModels([])
    setProbeError(null)
    setShowAddForm(false)
  }

  const pickEndpointPreset = (id: (typeof ENDPOINT_PRESETS)[number]["id"]) => {
    const preset = ENDPOINT_PRESETS.find((p) => p.id === id)
    if (!preset) return
    setEndpointPreset(id)
    setProbedModels([])
    setProbeError(null)
    setNewBackend((n) => ({
      ...n,
      name: preset.name || n.name,
      base_url: preset.base_url || n.base_url,
    }))
  }

  const loadEndpointModels = async () => {
    if (!newBackend.base_url.trim()) return
    setProbingModels(true)
    setProbeError(null)
    try {
      const result = await probeLlm(newBackend.base_url.trim(), newBackend.api_key.trim() || undefined)
      const models = result.available_models?.filter(Boolean) ?? []
      setProbedModels(models)
      if (result.status !== "healthy") {
        setProbeError(result.error || "Could not list models on that endpoint.")
        return
      }
      if (models.length && !newBackend.model.trim()) {
        setNewBackend((n) => ({ ...n, model: models[0] }))
      }
      if (!models.length) {
        setProbeError("Endpoint is up, but it listed no models. Type a model id below.")
      }
    } catch {
      setProbeError("Could not reach that endpoint from the server.")
    } finally {
      setProbingModels(false)
    }
  }

  const addBackend = async () => {
    if (!settings || !newBackend.name.trim() || !newBackend.base_url.trim() || !newBackend.model.trim()) return
    setAdding(true)
    const entry: LlmBackend = {
      name: newBackend.name.trim(),
      base_url: newBackend.base_url.trim(),
      model: newBackend.model.trim(),
      ...(newBackend.api_key.trim() ? { api_key: newBackend.api_key.trim() } : {}),
    }
    const backends = [...settings.llm.backends, entry]
    try {
      const updated = await updateSettings({ llm: { backends } })
      setSettings(updated)
      resetAddForm()
      flashSaved()
    } catch {
      // leave form open so the user can retry
      setSaveError("Couldn't add that backend.")
    } finally {
      setAdding(false)
    }
  }

  /** The bridge's shared secret, which becomes the backend's api_key.
   *
   * Only a loopback caller from this app's own origin may read it, which is
   * both true here (the browser runs on the same machine as the bridge) and
   * not true of a LAN peer or any other website. */
  const fetchBridgeKey = async (port: number): Promise<string> => {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 2000)
    try {
      const res = await fetch(`http://127.0.0.1:${port}/v1/bridge-key`, {
        signal: ctrl.signal,
      })
      if (!res.ok) throw new Error(String(res.status))
      const json = await res.json()
      if (!json?.key) throw new Error("no key in response")
      return json.key as string
    } finally {
      clearTimeout(timer)
    }
  }

  const connectPreset = async (preset: (typeof CLI_PRESETS)[number]) => {
    if (!settings) return
    setConnectingPreset(preset.id)
    try {
      // Always re-fetch and re-store the key, including for an already
      // connected preset: that is what repairs a backend whose stored key
      // is stale (an install predating bridge auth, or a regenerated key),
      // which would otherwise fail as a 401 at chat time with nothing in
      // the UI pointing back here.
      const key = await fetchBridgeKey(preset.port)
      const existing = settings.llm.backends.find((b) => b.name === preset.label)
      // Keep a real model the user picked, but replace a legacy tool-name
      // placeholder - that is what Connect repairs for an older install.
      const keepModel =
        existing?.model && !LEGACY_PLACEHOLDER_MODELS.has(existing.model)
          ? existing.model
          : CLI_DEFAULT_MODEL
      const entry: LlmBackend = {
        name: preset.label,
        base_url: `http://host.docker.internal:${preset.port}/v1`,
        model: keepModel,
        api_key: key,
      }
      const backends = existing
        ? settings.llm.backends.map((b) => (b.name === preset.label ? entry : b))
        : [...settings.llm.backends, entry]
      const updated = await updateSettings({ llm: { backends, active: entry.name } })
      setSettings(updated)
      flashSaved()
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 403) {
        setSaveError(`Connecting ${preset.label} requires an admin account.`)
      } else {
        setSaveError(`Couldn't connect ${preset.label}. Is the bridge still running?`)
      }
    } finally {
      setConnectingPreset(null)
    }
  }

  const copyLauncherCommand = () => {
    // navigator.clipboard is secure-context-only, so it is undefined when the
    // app is opened over plain HTTP at a LAN address. The old optional-chained
    // call then did nothing at all while still flashing "Copied", which is
    // worse than an honest failure - hence the execCommand fallback and the
    // conditional confirmation.
    if (!copyText(LAUNCHER_COMMAND)) {
      setSaveError("Couldn't copy automatically - select the command and copy it manually.")
      return
    }
    setCopiedLauncher(true)
    setTimeout(() => setCopiedLauncher(false), 1500)
  }

  const selectVoice = async (voice: string) => {
    if (!settings) return
    const prev = settings
    setSettings({ ...settings, tts: { default_voice: voice } })
    // Switch mid-read from the current sentence (voice is baked into audio).
    ttsEngine.setVoice(voice)
    try {
      await updateSettings({ tts: { default_voice: voice } })
      flashSaved()
    } catch {
      setSettings(prev)
      ttsEngine.setVoice(prev.tts.default_voice)
    }
  }

  const toggleExport = async () => {
    if (!settings) return
    const prev = settings
    const enabled = !settings.export.enabled
    setExportToggling(true)
    setSettings({ ...settings, export: { enabled } })
    try {
      await updateSettings({ export: { enabled } })
      flashSaved()
    } catch {
      setSettings(prev)
    } finally {
      setExportToggling(false)
    }
  }

  const runExportNow = async () => {
    setExportRunning(true)
    setExportMessage(null)
    try {
      const result = await exportNow()
      if (result.skipped) {
        setExportMessage({ text: "Export is not configured on this deployment", error: true })
      } else {
        const parts = [
          `${result.conversations_exported ?? 0} conversations`,
          `${result.flashcard_files_written ?? 0} flashcard decks`,
          `${result.podcasts_exported ?? 0} podcasts`,
        ]
        setExportMessage({ text: `Exported ${parts.join(", ")}`, error: false })
      }
    } catch {
      setExportMessage({ text: "Export failed - check that EXPORT_DIR is configured", error: true })
    } finally {
      setExportRunning(false)
    }
  }

  if (!open) return null

  const currentCat = CATEGORIES.find((c) => c.key === activeCategory)!

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass w-full max-w-md rounded-xl border border-surface shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface px-5 py-4">
          <div className="flex items-center gap-2">
            <currentCat.icon className="h-4 w-4 text-indigo" />
            <h2 className="font-display text-sm font-semibold text-cream">{currentCat.label}</h2>
          </div>
          <div className="flex items-center gap-2">
            {saveError ? (
              <span
                role="alert"
                title={saveError}
                className="flex max-w-[280px] items-center gap-1 text-[11px] font-medium text-[#ef4444]"
              >
                <AlertCircle className="h-3 w-3 shrink-0" />
                <span className="truncate">{saveError}</span>
              </span>
            ) : (
              saved && (
                <span className="flex items-center gap-1 text-[11px] font-medium text-[#4ade80]">
                  <Check className="h-3 w-3" />
                  Saved
                </span>
              )
            )}
            <button
              onClick={onClose}
              className="rounded-md p-1.5 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Category Tabs */}
        <div className="flex overflow-x-auto border-b border-surface px-3">
          {CATEGORIES.map((cat) => {
            const Icon = cat.icon
            const isActive = activeCategory === cat.key
            return (
              <button
                key={cat.key}
                onClick={() => { setActiveCategory(cat.key); setShowAddForm(false) }}
                className={`flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                  isActive
                    ? "border-indigo text-indigo"
                    : "border-transparent text-cream-dim hover:text-cream"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {cat.label}
              </button>
            )
          })}
        </div>

        {/* Body */}
        <div className="max-h-[calc(100vh-260px)] overflow-y-auto p-5">
          {activeCategory === "reader" ? (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-cream">Copy on select</p>
                  <p className="mt-0.5 text-xs text-cream-dim">
                    Copy selected text automatically. Off by default. You can still
                    copy from the note bar.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setCopyOnSelect(!copyOnSelect)}
                  aria-label="Copy on select"
                  aria-pressed={copyOnSelect}
                  className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
                    copyOnSelect ? "bg-indigo" : "bg-surface"
                  }`}
                >
                  <span
                    className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                      copyOnSelect ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            </div>
          ) : loading ? (
            <div className="space-y-3 py-6">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded-lg bg-surface/60" />
              ))}
            </div>
          ) : loadError || !settings ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <AlertCircle className="h-5 w-5 text-cream-dim" />
              <p className="text-sm text-cream-dim">Could not load settings</p>
            </div>
          ) : (
            <>
              {/* AI Model Category */}
              {activeCategory === "ai" && (
                <div className="space-y-5">
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-cream">Use a subscription you already have</p>
                    <p className="text-xs text-cream-dim">
                      Run this once on your machine - it auto-detects whichever of these CLIs you already
                      have installed and logged in, and nothing else needs any setup below.
                    </p>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 overflow-x-auto whitespace-nowrap rounded-md bg-void px-2.5 py-1.5 text-[11px] text-cream">
                        {LAUNCHER_COMMAND}
                      </code>
                      <button
                        onClick={copyLauncherCommand}
                        className="shrink-0 rounded-md p-1.5 text-cream-dim hover:bg-surface hover:text-cream"
                      >
                        {copiedLauncher ? (
                          <Check className="h-3.5 w-3.5 text-[#4ade80]" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                    <div className="space-y-2">
                      {CLI_PRESETS.map((preset) => {
                        const existing = settings.llm.backends.find((b) => b.name === preset.label)
                        const isActive = !!existing && existing.name === settings.llm.active
                        const isDetected = !!detected[preset.id]
                        return (
                          <div
                            key={preset.id}
                            className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                              isActive ? "border-indigo/40 bg-indigo/10" : "border-surface"
                            }`}
                          >
                            <Terminal className="h-4 w-4 shrink-0 text-cream-dim" />
                            <div className="min-w-0 flex-1">
                              <p className={`truncate text-sm font-medium ${isActive ? "text-indigo" : "text-cream"}`}>
                                {preset.label}
                              </p>
                              <p className="truncate text-xs text-cream-dim">
                                {existing
                                  ? isDetected
                                    ? isActive
                                      ? "Connected · active"
                                      : "Connected"
                                    : isActive
                                      ? "Offline · active - start the launcher"
                                      : "Offline - start the launcher"
                                  : isDetected
                                    ? "Detected - ready to connect"
                                    : "Not running - waiting for the launcher"}
                              </p>
                            </div>
                            {existing ? (
                              <div className="flex shrink-0 items-center gap-1">
                                {isActive ? (
                                  <>
                                    <CheckCircle2 className="h-4 w-4 text-indigo" />
                                    {/* The only way to repair a stored bridge
                                        key that has gone stale on the backend
                                        that is already active. */}
                                    <button
                                      onClick={() => connectPreset(preset)}
                                      disabled={!isDetected || connectingPreset === preset.id}
                                      title="Re-fetch this bridge's key"
                                      className="rounded p-1 text-cream-dim/40 transition-colors hover:bg-indigo/10 hover:text-indigo disabled:opacity-30 disabled:hover:bg-transparent"
                                    >
                                      <RefreshCw
                                        className={`h-3.5 w-3.5 ${connectingPreset === preset.id ? "animate-spin" : ""}`}
                                      />
                                    </button>
                                  </>
                                ) : (
                                  <button
                                    onClick={() => connectPreset(preset)}
                                    className="rounded-md px-2.5 py-1 text-xs font-medium text-indigo hover:bg-indigo/10"
                                  >
                                    Use
                                  </button>
                                )}
                                {settings.llm.backends.length > 1 && (
                                  <button
                                    onClick={() => deleteBackend(existing.name)}
                                    title="Disconnect"
                                    className="rounded p-1 text-cream-dim/40 hover:bg-[#ef4444]/10 hover:text-[#ef4444]"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                )}
                              </div>
                            ) : (
                              <button
                                onClick={() => connectPreset(preset)}
                                disabled={!isAdmin || !isDetected || connectingPreset === preset.id}
                                title={
                                  !isAdmin
                                    ? "An admin account is required to add this backend"
                                    : undefined
                                }
                                className="shrink-0 rounded-md px-2.5 py-1 text-xs font-medium text-indigo hover:bg-indigo/10 disabled:opacity-30 disabled:hover:bg-transparent"
                              >
                                {connectingPreset === preset.id ? "Connecting…" : "Connect"}
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  <div className="space-y-3">
                    <p className="text-xs font-medium text-cream">Self-hosted & custom endpoints</p>
                    {settings.llm.backends.filter((b) => !CLI_PRESET_NAMES.has(b.name)).length > 1 && (
                      <p className="text-xs text-cream-dim">
                        {settings.llm.backends.filter((b) => !CLI_PRESET_NAMES.has(b.name)).length} endpoints configured
                      </p>
                    )}
                  {settings.llm.backends.filter((b) => !CLI_PRESET_NAMES.has(b.name)).map((b) => {
                    const active = b.name === settings.llm.active
                    return (
                      <div
                        key={b.name}
                        onClick={() => !active && selectBackend(b.name)}
                        className={`flex items-center gap-3 rounded-lg px-3 py-2.5 border transition-colors ${
                          active
                            ? "border-indigo/40 bg-indigo/10"
                            : "border-surface cursor-pointer hover:border-surface/80"
                        }`}
                      >
                        <span
                          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                            active ? "border-indigo bg-indigo" : "border-cream-dim/40"
                          }`}
                        >
                          {active && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className={`truncate text-sm font-medium ${active ? "text-indigo" : "text-cream"}`}>
                            {b.name}
                          </p>
                          <p className="truncate text-xs text-cream-dim">{b.model}</p>
                        </div>
                        {settings.llm.backends.length > 1 && (
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteBackend(b.name) }}
                            className="shrink-0 rounded p-1 text-cream-dim/40 hover:text-[#ef4444] hover:bg-[#ef4444]/10"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    )
                  })}

                  {showAddForm ? (
                    <div className="mt-3 space-y-2 rounded-lg border border-surface p-3">
                      <p className="text-xs font-medium text-cream">Add endpoint</p>
                      <p className="text-[11px] text-cream-dim">
                        Pick a provider, load its models, then save - same idea as connecting Grok.
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {ENDPOINT_PRESETS.map((preset) => (
                          <button
                            key={preset.id}
                            type="button"
                            onClick={() => pickEndpointPreset(preset.id)}
                            className={`rounded-md px-2 py-1 text-[11px] ${
                              endpointPreset === preset.id
                                ? "bg-indigo/15 text-indigo"
                                : "text-cream-dim hover:bg-surface hover:text-cream"
                            }`}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          value={newBackend.name}
                          onChange={(e) => setNewBackend((n) => ({ ...n, name: e.target.value }))}
                          placeholder="Name"
                          className="rounded-md bg-void px-3 py-2 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo/30"
                        />
                        {probedModels.length > 0 ? (
                          <select
                            value={newBackend.model}
                            onChange={(e) => setNewBackend((n) => ({ ...n, model: e.target.value }))}
                            className="rounded-md bg-void px-3 py-2 text-xs text-cream focus:outline-none focus:ring-1 focus:ring-indigo/30"
                          >
                            {probedModels.map((m) => (
                              <option key={m} value={m}>
                                {m}
                              </option>
                            ))}
                            {newBackend.model && !probedModels.includes(newBackend.model) && (
                              <option value={newBackend.model}>{newBackend.model}</option>
                            )}
                          </select>
                        ) : (
                          <input
                            value={newBackend.model}
                            onChange={(e) => setNewBackend((n) => ({ ...n, model: e.target.value }))}
                            placeholder="Model"
                            className="rounded-md bg-void px-3 py-2 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo/30"
                          />
                        )}
                      </div>
                      <input
                        value={newBackend.base_url}
                        onChange={(e) => {
                          setNewBackend((n) => ({ ...n, base_url: e.target.value }))
                          setProbedModels([])
                        }}
                        placeholder="Base URL"
                        className="w-full rounded-md bg-void px-3 py-2 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo/30"
                      />
                      <input
                        type="password"
                        value={newBackend.api_key}
                        onChange={(e) => setNewBackend((n) => ({ ...n, api_key: e.target.value }))}
                        placeholder="API key (optional)"
                        className="w-full rounded-md bg-void px-3 py-2 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo/30"
                      />
                      {probeError && <p className="text-[11px] text-[#f87171]">{probeError}</p>}
                      <div className="flex items-center gap-2 pt-1">
                        <button
                          type="button"
                          onClick={loadEndpointModels}
                          disabled={probingModels || !newBackend.base_url.trim()}
                          className="rounded-md px-3 py-1.5 text-xs font-medium text-indigo hover:bg-indigo/10 disabled:opacity-50"
                        >
                          {probingModels ? "Loading models…" : "Load models"}
                        </button>
                        <button
                          onClick={addBackend}
                          disabled={adding || !newBackend.name.trim() || !newBackend.base_url.trim() || !newBackend.model.trim()}
                          className="gradient-primary rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                        >
                          {adding ? "Saving..." : "Save"}
                        </button>
                        <button
                          onClick={resetAddForm}
                          className="rounded-md px-3 py-1.5 text-xs text-cream-dim hover:text-cream"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : isAdmin ? (
                    <button
                      onClick={() => setShowAddForm(true)}
                      className="flex items-center gap-1.5 text-xs text-cream-dim hover:text-cream transition-colors"
                    >
                      <Plus className="h-3 w-3" />
                      Add endpoint
                    </button>
                  ) : (
                    <p className="text-[11px] text-cream-dim">
                      An admin can add OpenAI-compatible endpoints here. You can still pick a model
                      on any endpoint already connected.
                    </p>
                  )}
                  </div>
                </div>
              )}

              {/* Voice Category */}
              {activeCategory === "voice" && (
                <div className="space-y-1.5">
                  {voices.length === 0 ? (
                    <p className="text-sm text-cream-dim py-2">
                      Default: {settings.tts.default_voice}
                    </p>
                  ) : (
                    voices.map((v) => {
                      const active = v.id === settings.tts.default_voice
                      const isPreviewing = previewVoice === v.id
                      return (
                        <div
                          key={v.id}
                          onClick={() => !active && selectVoice(v.id)}
                          className={`flex items-center gap-3 rounded-lg px-3 py-2.5 border transition-colors ${
                            active
                              ? "border-indigo/40 bg-indigo/10"
                              : "border-surface cursor-pointer hover:border-surface/80"
                          }`}
                        >
                          <span
                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                              active ? "border-indigo bg-indigo" : "border-cream-dim/40"
                            }`}
                          >
                            {active && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className={`truncate text-sm font-medium ${active ? "text-indigo" : "text-cream"}`}>
                              {v.label}
                            </p>
                          </div>
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              void playPreview(v.id)
                            }}
                            className={`shrink-0 rounded-md p-1.5 transition-colors ${
                              isPreviewing && previewStatus === "playing"
                                ? "text-indigo bg-indigo/10"
                                : "text-cream-dim hover:text-cream hover:bg-surface"
                            }`}
                          >
                            {isPreviewing && previewStatus === "loading" ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : isPreviewing ? (
                              <Square className="h-3 w-3 fill-current" />
                            ) : (
                              <Play className="h-3.5 w-3.5" />
                            )}
                          </button>
                        </div>
                      )
                    })
                  )}
                </div>
              )}

              {/* Export Category */}
              {activeCategory === "export" && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-cream">Mirror to disk</p>
                      <p className="text-xs text-cream-dim mt-0.5">
                        Syncs chats, flashcards, and podcasts hourly
                      </p>
                    </div>
                    <button
                      onClick={() => !exportToggling && toggleExport()}
                      className={`relative shrink-0 h-5 w-9 rounded-full transition-colors ${
                        settings.export.enabled ? "bg-indigo" : "bg-surface"
                      }`}
                    >
                      <span
                        className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                          settings.export.enabled ? "translate-x-4" : "translate-x-0"
                        }`}
                      />
                    </button>
                  </div>

                  {settings.export.enabled && (
                    <div className="flex items-center gap-3">
                      <button
                        onClick={runExportNow}
                        disabled={exportRunning}
                        className="flex items-center gap-1.5 rounded-md bg-surface px-3 py-1.5 text-xs font-medium text-cream transition-colors hover:bg-surface/80 disabled:opacity-50"
                      >
                        {exportRunning ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <FolderOutput className="h-3.5 w-3.5" />
                        )}
                        Export now
                      </button>
                      {exportMessage && (
                        <p className={`text-xs ${exportMessage.error ? "text-[#ef4444]" : "text-cream-dim"}`}>
                          {exportMessage.text}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
              {/* Admin Category */}
              {activeCategory === "admin" && (
                <div className="space-y-4">
                  <div className="rounded-lg border border-indigo/20 bg-indigo/5 p-4">
                    <div className="flex items-start gap-3">
                      <Shield className="mt-0.5 h-5 w-5 text-indigo" />
                      <div>
                        <h3 className="font-medium text-indigo">Admin Panel</h3>
                        <p className="mt-1 text-sm text-cream-dim">
                          Full user management and system controls.
                        </p>
                      </div>
                    </div>
                  </div>

                  <a
                    href="/admin"
                    className="block w-full rounded-lg border border-surface bg-panel px-4 py-3 text-left transition-colors hover:border-indigo/40 hover:bg-indigo/5"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-cream">Go to Admin Panel</p>
                        <p className="text-xs text-cream-dim mt-0.5">
                          Manage users, promote/demote admins, delete accounts
                        </p>
                      </div>
                      <Shield className="h-5 w-5 text-indigo" />
                    </div>
                  </a>
                </div>
              )}

            </>
          )}
        </div>
      </div>
    </div>
  )
}
