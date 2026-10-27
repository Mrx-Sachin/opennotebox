"use client"

import { useState } from "react"
import { NoteColor } from "@/services/types"
import { useNotes } from "@/stores/notes"

const PRESET_HEX = ["#fb7185", "#fb923c", "#38bdf8", "#a78bfa", "#f472b6", "#94a3b8"]

interface AddColorFormProps {
  onAdded: (color: NoteColor) => void
  onCancel: () => void
}

export default function AddColorForm({ onAdded, onCancel }: AddColorFormProps) {
  const addColor = useNotes((s) => s.addColor)
  const [name, setName] = useState("")
  const [hex, setHex] = useState("#fb7185")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setError("Name this colour")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const color = await addColor(trimmed, hex)
      onAdded(color)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not add colour")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 w-56 rounded-lg border border-surface bg-panel p-2.5 shadow-glow">
      <label className="block text-[10px] font-semibold uppercase tracking-wide text-cream-dim">
        Name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit()
            if (e.key === "Escape") onCancel()
          }}
          placeholder="e.g. method"
          className="mt-1 w-full rounded-md border border-surface bg-void px-2 py-1 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
        />
      </label>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="color"
          value={hex}
          onChange={(e) => setHex(e.target.value)}
          className="h-7 w-8 cursor-pointer rounded border border-surface bg-void p-0"
          title="Pick a colour"
        />
        <div className="flex flex-1 flex-wrap gap-1">
          {PRESET_HEX.map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setHex(h)}
              className={`h-4 w-4 rounded-full ${hex === h ? "ring-2 ring-cream" : ""}`}
              style={{ background: h }}
              aria-label={h}
            />
          ))}
        </div>
      </div>
      {error && <p className="mt-1.5 text-[11px] text-rust">{error}</p>}
      <div className="mt-2 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-1 text-[11px] text-cream-dim hover:text-cream"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={() => void submit()}
          className="rounded bg-indigo/20 px-2 py-1 text-[11px] font-medium text-indigo hover:bg-indigo/30 disabled:opacity-50"
        >
          {saving ? "Adding…" : "Add"}
        </button>
      </div>
    </div>
  )
}
