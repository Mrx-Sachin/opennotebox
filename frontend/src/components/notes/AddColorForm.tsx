"use client"

import { useState } from "react"
import { NoteColor } from "@/services/types"
import { useNotes } from "@/stores/notes"

const PRESET_COLORS = [
  { name: "rose", hex: "#fb7185" },
  { name: "orange", hex: "#fb923c" },
  { name: "sky", hex: "#38bdf8" },
  { name: "purple", hex: "#a78bfa" },
  { name: "pink", hex: "#f472b6" },
  { name: "slate", hex: "#94a3b8" },
]

interface AddColorFormProps {
  onAdded: (color: NoteColor) => void
  onCancel: () => void
}

export default function AddColorForm({ onAdded, onCancel }: AddColorFormProps) {
  const addColor = useNotes((s) => s.addColor)
  const [name, setName] = useState("purple")
  const [hint, setHint] = useState("")
  const [hex, setHex] = useState("#a78bfa")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSelectPreset = (p: { name: string; hex: string }) => {
    setHex(p.hex)
    if (!name || PRESET_COLORS.some((preset) => preset.name.toLowerCase() === name.toLowerCase())) {
      setName(p.name)
    }
  }

  const submit = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError("Name this colour")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const color = await addColor(trimmedName, hex, hint.trim() || undefined)
      onAdded(color)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not add colour")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 w-full rounded-lg border border-surface bg-panel p-2.5 shadow-glow">
      <div className="space-y-2">
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
          Colour name
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit()
              if (e.key === "Escape") onCancel()
            }}
            placeholder="e.g. purple, rose, cyan"
            className="mt-1 w-full rounded-md border border-surface bg-void px-2 py-1 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
          />
        </label>
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
          What for <span>(purpose / category)</span>
          <input
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit()
              if (e.key === "Escape") onCancel()
            }}
            placeholder="e.g. facts, methodology, quote"
            className="mt-1 w-full rounded-md border border-surface bg-void px-2 py-1 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
          />
        </label>
      </div>

      <div className="mt-2.5 flex items-center gap-2">
        <input
          type="color"
          value={hex}
          onChange={(e) => setHex(e.target.value)}
          className="h-7 w-8 cursor-pointer rounded border border-surface bg-void p-0"
          title="Pick custom hex colour"
        />
        <div className="flex flex-1 flex-wrap gap-1.5">
          {PRESET_COLORS.map((p) => (
            <button
              key={p.hex}
              type="button"
              onClick={() => handleSelectPreset(p)}
              className={`h-4 w-4 rounded-full transition-transform ${hex.toLowerCase() === p.hex.toLowerCase() ? "scale-125 ring-2 ring-cream" : "hover:scale-110"}`}
              style={{ background: p.hex }}
              title={p.name}
              aria-label={p.name}
            />
          ))}
        </div>
      </div>

      {error && <p className="mt-1.5 text-[11px] text-rust">{error}</p>}

      <div className="mt-3 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2.5 py-1 text-[11px] text-cream-dim hover:text-cream"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={saving || !name.trim()}
          onClick={() => void submit()}
          className="rounded bg-indigo/20 px-2.5 py-1 text-[11px] font-medium text-indigo hover:bg-indigo/30 disabled:opacity-50"
        >
          {saving ? "Adding…" : "Add colour"}
        </button>
      </div>
    </div>
  )
}
