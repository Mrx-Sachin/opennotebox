"use client"

import { useState } from "react"
import { Pencil, Trash2, X } from "lucide-react"
import { useNotes } from "@/stores/notes"
import { colorLabel } from "@/lib/noteColors"
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
  const [hexDraft, setHexDraft] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const startEdit = (id: string, name: string, hex: string) => {
    setEditingId(id)
    setNameDraft(name)
    setHexDraft(hex)
    setError(null)
  }

  const saveEdit = async () => {
    if (!editingId) return
    try {
      await renameColor(editingId, nameDraft.trim(), hexDraft)
      setEditingId(null)
      setError(null)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === "string" ? detail : "Could not update colour")
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
      <ul className="space-y-1.5">
        {colors.map((c) => (
          <li key={c.id} className="flex items-center gap-2 text-xs">
            <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: c.hex }} />
            {editingId === c.id ? (
              <>
                <input
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  className="min-w-0 flex-1 rounded border border-surface bg-panel px-1.5 py-0.5 text-xs text-cream focus:outline-none focus:ring-1 focus:ring-indigo"
                />
                <input
                  type="color"
                  value={hexDraft}
                  onChange={(e) => setHexDraft(e.target.value)}
                  className="h-6 w-7 cursor-pointer rounded border border-surface bg-panel p-0"
                />
                <button
                  type="button"
                  onClick={() => void saveEdit()}
                  className="text-[11px] font-medium text-indigo hover:text-cream"
                >
                  Save
                </button>
              </>
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate text-cream">
                  {c.name}
                  {c.hint && (
                    <span className="ml-1.5 text-[10px] text-cream-dim">{c.hint}</span>
                  )}
                </span>
                {c.built_in ? (
                  <span className="text-[10px] text-cream-dim">default</span>
                ) : (
                  <>
                    <button
                      type="button"
                      title={`Rename ${colorLabel(c)}`}
                      onClick={() => startEdit(c.id, c.name, c.hex)}
                      className="rounded p-0.5 text-cream-dim hover:text-cream"
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      title={`Remove ${c.name}`}
                      onClick={() => void handleDelete(c.id)}
                      className="rounded p-0.5 text-cream-dim hover:text-rust"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </>
                )}
              </>
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
          onClick={() => setAdding(true)}
          className="mt-2 text-[11px] font-medium text-indigo hover:text-cream"
        >
          + Add a colour
        </button>
      )}
    </div>
  )
}
