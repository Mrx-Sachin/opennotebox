"use client"

import { useState } from "react"
import { Pencil, Trash2, X } from "lucide-react"
import { useNotes } from "@/stores/notes"
import { colorLabel } from "@/lib/noteColors"
import { NoteColor } from "@/services/types"
import AddColorForm from "./AddColorForm"

interface ColorManagerProps {
  onClose: () => void
}

export default function ColorManager({ onClose }: ColorManagerProps) {
  const colors = useNotes((s) => s.colors)
  const renameColor = useNotes((s) => s.renameColor)
  const removeColor = useNotes((s) => s.removeColor)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [nameDraft, setNameDraft] = useState("")
  const [hintDraft, setHintDraft] = useState("")
  const [hexDraft, setHexDraft] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)

  const startEdit = (color: NoteColor) => {
    setEditingId(color.id)
    setNameDraft(color.name)
    setHintDraft(color.hint || "")
    setHexDraft(color.hex)
    setError(null)
    setAdding(false)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setError(null)
  }

  const saveEdit = async () => {
    if (!editingId) return
    const activeColor = colors.find((c) => c.id === editingId)
    if (!activeColor) return
    setSaving(true)
    try {
      await renameColor(
        editingId,
        activeColor.built_in ? activeColor.name : nameDraft.trim(),
        hexDraft,
        hintDraft.trim() || null
      )
      setEditingId(null)
      setError(null)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not update colour")
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    setError(null)
    try {
      await removeColor(id)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not remove colour")
    }
  }

  return (
    <div className="rounded-lg border border-surface bg-void/80 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-cream-dim">
          Highlight colours
        </h4>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-0.5 text-cream-dim hover:text-cream"
          title="Close"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <ul className="space-y-2">
        {colors.map((c) => (
          <li key={c.id} className="text-xs">
            {editingId === c.id ? (
              <div className="rounded-md border border-surface bg-panel/70 p-2">
                <div className="mb-2 flex items-center gap-2">
                  <input
                    type="color"
                    value={hexDraft}
                    onChange={(e) => setHexDraft(e.target.value)}
                    className="h-6 w-7 cursor-pointer rounded border border-surface bg-void p-0"
                    title="Pick colour"
                  />
                  {!c.built_in && (
                    <input
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      placeholder="Colour name"
                      className="w-24 rounded border border-surface bg-void px-1.5 py-0.5 text-xs text-cream focus:outline-none focus:ring-1 focus:ring-indigo"
                      title="Colour name"
                    />
                  )}
                  <input
                    value={hintDraft}
                    onChange={(e) => setHintDraft(e.target.value)}
                    placeholder="What for? (e.g. claim, facts)"
                    className="min-w-0 flex-1 rounded border border-surface bg-void px-1.5 py-0.5 text-xs text-cream placeholder:text-cream-dim/50 focus:outline-none focus:ring-1 focus:ring-indigo"
                    title="What is this colour for?"
                  />
                </div>
                <div className="flex justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={cancelEdit}
                    className="rounded px-2 py-0.5 text-[10px] text-cream-dim hover:text-cream"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={saving || (!c.built_in && !nameDraft.trim())}
                    onClick={() => void saveEdit()}
                    className="rounded bg-indigo/20 px-2 py-0.5 text-[10px] font-medium text-indigo hover:bg-indigo/30 disabled:opacity-50"
                  >
                    {saving ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: c.hex }} />
                <div className="flex min-w-0 flex-1 items-baseline gap-1.5 truncate">
                  <span className="font-medium text-cream">{c.name}</span>
                  {c.hint && (
                    <span className="truncate text-[11px] text-cream-dim">{c.hint}</span>
                  )}
                </div>
                {c.built_in && (
                  <span className="text-[10px] text-cream-dim">default</span>
                )}
                <button
                  type="button"
                  title={`Edit ${colorLabel(c)}`}
                  onClick={() => startEdit(c)}
                  className="rounded p-0.5 text-cream-dim hover:text-cream"
                >
                  <Pencil className="h-3 w-3" />
                </button>
                {!c.built_in && (
                  <button
                    type="button"
                    title={`Remove ${c.name}`}
                    onClick={() => void handleDelete(c.id)}
                    className="rounded p-0.5 text-cream-dim hover:text-rust"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-[11px] text-rust">{error}</p>}
      {adding ? (
        <AddColorForm onAdded={() => setAdding(false)} onCancel={() => setAdding(false)} />
      ) : (
        <button
          type="button"
          onClick={() => {
            setAdding(true)
            setEditingId(null)
          }}
          className="mt-2.5 text-[11px] font-medium text-indigo hover:text-cream"
        >
          + Add a colour
        </button>
      )}
    </div>
  )
}
