"use client"

import { useEffect, useState } from "react"

/**
 * Viewport class for layout decisions that CSS alone cannot express.
 *
 * Most responsive work belongs in Tailwind's breakpoints; this exists for the
 * cases where the *behaviour* differs, not just the styling - whether a side
 * panel is a column or an overlay, whether opening one closes another. Those
 * are decisions about component structure, and encoding them in class names
 * would mean rendering both structures and hiding one.
 *
 * Breakpoints, and why these:
 *   phone   < 768   one pane at a time; a second column leaves neither usable.
 *   compact < 1280  the reader plus at most one overlaid panel. This is the
 *                   15" laptop case, where three columns technically fit and
 *                   the result is a page of text clipped on both edges.
 *   wide    >=1280  three real columns.
 */
export type Viewport = "phone" | "compact" | "wide"

export const PHONE_MAX = 767
export const COMPACT_MAX = 1279

function classify(width: number): Viewport {
  if (width <= PHONE_MAX) return "phone"
  if (width <= COMPACT_MAX) return "compact"
  return "wide"
}

export function useViewport(): Viewport {
  // "wide" on the server and the first client paint, corrected on mount.
  // Guessing narrow instead would mean every desktop load briefly renders the
  // mobile structure and then rebuilds - a visible flash on the common case.
  const [viewport, setViewport] = useState<Viewport>("wide")

  useEffect(() => {
    const read = () => setViewport(classify(window.innerWidth))
    read()
    window.addEventListener("resize", read)
    // Rotating a phone changes innerWidth before resize fires in some
    // browsers; orientationchange covers that.
    window.addEventListener("orientationchange", read)
    return () => {
      window.removeEventListener("resize", read)
      window.removeEventListener("orientationchange", read)
    }
  }, [])

  return viewport
}
