import type { NoteColor } from "@/services/types"

/** Built-in palette. Names stay amber / green / indigo; hints are the
 * Zotero-style meaning (claim / definition / question). */
export const DEFAULT_NOTE_COLORS: NoteColor[] = [
  { id: "amber", name: "amber", hex: "#fbbf24", built_in: true, hint: "claim" },
  { id: "green", name: "green", hex: "#34d399", built_in: true, hint: "definition" },
  { id: "indigo", name: "indigo", hex: "#818cf8", built_in: true, hint: "question" },
]

const FALLBACK_HEX: Record<string, string> = Object.fromEntries(
  DEFAULT_NOTE_COLORS.map((c) => [c.id, c.hex])
)

export function colorById(colors: NoteColor[], id: string): NoteColor | undefined {
  return colors.find((c) => c.id === id)
}

export function colorHex(colors: NoteColor[], id: string): string {
  return colorById(colors, id)?.hex || FALLBACK_HEX[id] || FALLBACK_HEX.amber
}

export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "")
  if (h.length !== 6) return `rgba(251, 191, 36, ${alpha})`
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) {
    return `rgba(251, 191, 36, ${alpha})`
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function colorLabel(color: NoteColor): string {
  return color.hint ? `${color.name} (${color.hint})` : color.name
}
