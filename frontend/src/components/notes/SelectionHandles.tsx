"use client"

import { useEffect, useState } from "react"
import { useNotes } from "@/stores/notes"
import { useViewport } from "@/hooks/useViewport"
import {
  extendSelectionEdge,
  pagesFromSelection,
  selectionHandlePoints,
} from "@/lib/textSelect"

/** True on a real phone (narrow) or any coarse pointer (touch laptop). */
function useTouchSelect(): boolean {
  const viewport = useViewport()
  const [coarse, setCoarse] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia("(pointer: coarse)")
    const read = () => setCoarse(mq.matches)
    read()
    mq.addEventListener("change", read)
    return () => mq.removeEventListener("change", read)
  }, [])
  return viewport === "phone" || coarse
}

/**
 * Start/end drag handles so a long-pressed word can be grown on a phone.
 * Desktop keeps native mouse selection and never mounts these.
 */
export default function SelectionHandles() {
  const selection = useNotes((s) => s.selection)
  const setSelection = useNotes((s) => s.setSelection)
  const touch = useTouchSelect()
  const [pts, setPts] = useState<ReturnType<typeof selectionHandlePoints>>(null)

  const show = !!selection && touch

  useEffect(() => {
    if (!show) {
      setPts(null)
      return
    }
    const refresh = () => setPts(selectionHandlePoints())
    refresh()
    window.addEventListener("scroll", refresh, true)
    window.addEventListener("resize", refresh)
    document.addEventListener("selectionchange", refresh)
    return () => {
      window.removeEventListener("scroll", refresh, true)
      window.removeEventListener("resize", refresh)
      document.removeEventListener("selectionchange", refresh)
    }
  }, [show, selection?.quote, selection?.page])

  if (!show || !pts) return null

  const drag = (edge: "start" | "end") => (e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {
      /* capture is best-effort */
    }
    const sel0 = useNotes.getState().selection
    if (!sel0) return

    const move = (ev: PointerEvent) => {
      const text = extendSelectionEdge(edge, ev.clientX, ev.clientY)
      if (!text) return
      const live = window.getSelection()
      const rect = live?.rangeCount ? live.getRangeAt(0).getBoundingClientRect() : null
      const pages = live ? pagesFromSelection(live) : null
      setSelection({
        ...sel0,
        quote: text,
        page: pages?.start ?? sel0.page,
        endPage: pages && pages.end !== pages.start ? pages.end : undefined,
        x: rect ? rect.left + rect.width / 2 : sel0.x,
        y: rect ? rect.bottom : sel0.y,
      })
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
  }

  return (
    <>
      <button
        type="button"
        data-sel-handle="start"
        aria-label="Move selection start"
        className="sel-handle sel-handle-start"
        style={{ left: pts.start.x, top: pts.start.y }}
        onPointerDown={drag("start")}
      >
        <span className="sel-handle-knob" />
        <span className="sel-handle-stem" />
      </button>
      <button
        type="button"
        data-sel-handle="end"
        aria-label="Move selection end"
        className="sel-handle sel-handle-end"
        style={{ left: pts.end.x, top: pts.end.y }}
        onPointerDown={drag("end")}
      >
        <span className="sel-handle-stem" />
        <span className="sel-handle-knob" />
      </button>
    </>
  )
}
