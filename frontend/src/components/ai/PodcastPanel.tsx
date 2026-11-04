"use client"

import { useEffect, useState } from "react"
import {
  Podcast,
  PodcastLength,
  createPodcast,
  getPodcasts,
  deletePodcast,
  getSummaries,
} from "@/services/api"
import { useReader } from "@/stores/reader"
import { ttsEngine } from "@/services/ttsEngine"
import { Loader2, Play, Pause, Trash2, Mic, AlertCircle } from "lucide-react"

interface PodcastPanelProps {
  docId: number
  docTitle: string
}

function fmt(t?: number | null): string {
  if (!t || !isFinite(t)) return ""
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

const LENGTHS: { value: PodcastLength; label: string }[] = [
  { value: "short", label: "~5 min" },
  { value: "medium", label: "~10 min" },
  { value: "long", label: "~20 min" },
  { value: "deep_dive", label: "~35 min" },
]

export default function PodcastPanel({ docId, docTitle }: PodcastPanelProps) {
  const [podcasts, setPodcasts] = useState<Podcast[]>([])
  const [sections, setSections] = useState<string[]>([])
  const [scope, setScope] = useState<"full" | "section">("full")
  const [sectionTitle, setSectionTitle] = useState("")
  const [length, setLength] = useState<PodcastLength>("medium")
  const [creating, setCreating] = useState(false)
  const [loadingId, setLoadingId] = useState<number | null>(null)
  const activePodcast = useReader((state) => state.activePodcast)
  const playingId = activePodcast?.id ?? null
  const ttsStatus = useReader((state) => state.tts.status)

  const refresh = () => getPodcasts(docId).then(setPodcasts).catch(() => {})

  useEffect(() => {
    refresh()
    getSummaries(docId, "section")
      .then((s) => {
        const titles = s.map((x) => x.section_title).filter(Boolean) as string[]
        setSections(titles)
        if (titles.length > 0) setSectionTitle(titles[0])
      })
      .catch(() => setSections([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId])

  useEffect(() => {
    const hasGenerating = podcasts.some((p) => p.status === "generating")
    if (!hasGenerating) return
    const interval = setInterval(refresh, 3000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [podcasts, docId])

  const handleCreate = async () => {
    setCreating(true)
    try {
      await createPodcast(docId, scope, scope === "section" ? sectionTitle : undefined, length)
      await refresh()
    } catch (err) {
      console.error("Failed to create podcast:", err)
    } finally {
      setCreating(false)
    }
  }

  const handlePlay = async (p: Podcast) => {
    if (playingId === p.id) {
      ttsEngine.toggle()
      return
    }
    setLoadingId(p.id)
    try {
      await ttsEngine.playPodcast(p)
    } catch (err) {
      console.error("Failed to play podcast:", err)
    } finally {
      setLoadingId(null)
    }
  }

  const handleDelete = async (id: number) => {
    if (playingId === id) {
      ttsEngine.stop()
    }
    try {
      await deletePodcast(id)
      refresh()
    } catch (err) {
      console.error("Failed to delete podcast:", err)
    }
  }

  return (
    <div className="rounded-xl border border-surface bg-void/40 p-4 space-y-3.5 shadow-sm">
      <div className="flex items-center gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-md border border-purple-500/30 bg-purple-950/40 text-purple-300 shadow-sm">
          <Mic className="h-3.5 w-3.5" />
        </div>
        <div>
          <h3 className="text-xs font-semibold text-cream">Podcast Studio</h3>
          <p className="text-[10px] text-cream-dim">Generate audio discussions from your document</p>
        </div>
      </div>

      <div className="space-y-2.5">
        <div className="flex gap-1 rounded-lg border border-surface/50 bg-void p-1">
          <button
            type="button"
            onClick={() => setScope("full")}
            className={`flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition-[background-color,color] duration-150 ${
              scope === "full" ? "gradient-primary text-white" : "text-cream-dim hover:text-cream"
            }`}
          >
            Entire doc
          </button>
          <button
            type="button"
            onClick={() => setScope("section")}
            disabled={sections.length === 0}
            className={`flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition-[background-color,color] duration-150 disabled:opacity-40 ${
              scope === "section" ? "gradient-primary text-white" : "text-cream-dim hover:text-cream"
            }`}
          >
            Chapter
          </button>
        </div>

        {scope === "section" && (
          <select
            value={sectionTitle}
            onChange={(e) => setSectionTitle(e.target.value)}
            className="w-full rounded-lg border border-surface bg-void px-2 py-1.5 text-xs text-cream focus:border-indigo/60 focus:outline-none"
          >
            {sections.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}

        <div className="flex gap-1 rounded-lg border border-surface/50 bg-void p-1" title="Podcast length">
          {LENGTHS.map((l) => (
            <button
              key={l.value}
              type="button"
              onClick={() => setLength(l.value)}
              title={`${l.value} podcast`}
              className={`flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition-[background-color,color] duration-150 ${
                length === l.value ? "bg-indigo/20 text-indigo" : "text-cream-dim hover:text-cream"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={handleCreate}
          disabled={creating || (scope === "section" && !sectionTitle)}
          className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:opacity-50"
        >
          {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
          <span>Generate podcast</span>
        </button>
      </div>

      {podcasts.length > 0 && (
        <div className="space-y-2 border-t border-surface/60 pt-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-cream-dim">
              Episodes ({podcasts.length})
            </span>
          </div>
          <ul className="reader-scroll max-h-80 space-y-1.5 overflow-y-auto pr-0.5">
            {podcasts.map((p) => (
              <li
                key={p.id}
                className="group flex items-center gap-2.5 rounded-lg border border-surface bg-panel px-2.5 py-2 transition-[border-color,background-color] duration-150 hover:border-surface/80"
              >
                {p.status === "generating" ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo" />
                ) : p.status === "failed" ? (
                  <AlertCircle className="h-4 w-4 shrink-0 text-[#ef4444]" />
                ) : (
                  <button
                    type="button"
                    onClick={() => handlePlay(p)}
                    disabled={loadingId === p.id}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo/10 text-indigo transition-[background-color,color] duration-150 hover:bg-indigo/20 disabled:opacity-50"
                    title={
                      playingId === p.id && ttsStatus === "playing"
                        ? "Pause"
                        : "Play in the bottom player"
                    }
                  >
                    {loadingId === p.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : playingId === p.id && ttsStatus === "playing" ? (
                      <Pause className="h-3.5 w-3.5" />
                    ) : (
                      <Play className="h-3.5 w-3.5 ml-0.5" />
                    )}
                  </button>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-cream">
                    {p.transcript?.title ||
                      (p.scope === "section" ? p.section_title : docTitle)}
                  </p>
                  <p className="text-[10px] text-cream-dim">
                    {p.status === "generating"
                      ? "Writing script & recording..."
                      : p.status === "failed"
                        ? p.error || "Generation failed"
                        : fmt(p.duration)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(p.id)}
                  title="Delete podcast"
                  className="hidden shrink-0 rounded p-1 text-cream-dim transition-colors hover:text-[#ef4444] group-hover:block"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
