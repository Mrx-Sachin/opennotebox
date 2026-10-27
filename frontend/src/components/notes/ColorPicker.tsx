"use client"

import { useState } from "react"
import { Plus } from "lucide-react"
import { NoteColor } from "@/services/types"
import { colorLabel } from "@/lib/noteColors"
import AddColorForm from "./AddColorForm"

interface ColorPickerProps {
  colors: NoteColor[]
  value: string
  onChange: (id: string) => void
  onAdded?: (color: NoteColor) => void
  size?: "sm" | "md"
}

export default function ColorPicker({
  colors,
  value,
  onChange,
  onAdded,
  size = "md",
}: ColorPickerProps) {
  const [adding, setAdding] = useState(false)
  const dim = size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5"

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-1.5">
        {colors.map((c) => {
          const selected = c.id === value
          return (
            <button
              key={c.id}
              type="button"
              title={colorLabel(c)}
              aria-label={colorLabel(c)}
              aria-pressed={selected}
              onClick={() => onChange(c.id)}
              className={`rounded-full border-2 transition-transform ${
                selected
                  ? "scale-110 border-cream"
                  : "border-transparent opacity-80 hover:scale-110 hover:opacity-100"
              }`}
            >
              <span className={`block rounded-full ${dim}`} style={{ background: c.hex }} />
            </button>
          )
        })}
        <button
          type="button"
          title="Add a colour"
          aria-label="Add a colour"
          onClick={() => setAdding((v) => !v)}
          className="rounded-full border border-dashed border-cream-dim/40 p-0.5 text-cream-dim hover:border-cream hover:text-cream"
        >
          <Plus className={dim} />
        </button>
      </div>
      {adding && (
        <AddColorForm
          onAdded={(color) => {
            onAdded?.(color)
            onChange(color.id)
            setAdding(false)
          }}
          onCancel={() => setAdding(false)}
        />
      )}
    </div>
  )
}
