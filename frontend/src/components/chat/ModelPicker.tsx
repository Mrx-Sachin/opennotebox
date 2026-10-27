"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Search, Check, Loader2, Cpu, X } from "lucide-react"
import { LlmBackend, getLlmHealth } from "@/services/api"

/** Port of the local CLI bridge a backend points at, or null if it isn't one.
 * Bridges always run on the user's own machine, so only host-local hostnames
 * qualify. The backend reaches them via host.docker.internal (it lives in a
 * container); the browser cannot resolve that, so it asks loopback instead. */
export function bridgePort(baseUrl: string): string | null {
  const m = /^https?:\/\/(host\.docker\.internal|127\.0\.0\.1|localhost):(\d+)/.exec(baseUrl)
  return m ? m[2] : null
}

/** Host shown under a group header, so two backends on the same box are
 * still distinguishable at a glance. */
function endpointLabel(baseUrl: string): string {
  try {
    const u = new URL(baseUrl)
    return u.host
  } catch {
    return baseUrl
  }
}

interface Row {
  backend: LlmBackend
  model: string
}

interface Group {
  backend: LlmBackend
  isBridge: boolean
  loading: boolean
  offline: boolean
  rows: Row[]
}

interface ModelPickerProps {
  open: boolean
  onClose: () => void
  backends: LlmBackend[]
  activeName: string
  onSelect: (backend: LlmBackend, model: string) => void
}

export default function ModelPicker({
  open,
  onClose,
  backends,
  activeName,
  onSelect,
}: ModelPickerProps) {
  const [query, setQuery] = useState("")
  const [cliModels, setCliModels] = useState<Record<string, string[]>>({})
  const [probing, setProbing] = useState<Record<string, boolean>>({})
  // A bridge whose /v1/models probe failed. Previously this state was
  // invisible - a dead bridge listed its one configured model exactly like
  // a live one, and picking it produced no feedback until the chat request
  // itself failed.
  const [offline, setOffline] = useState<Record<string, boolean>>({})
  // Keyboard highlight only. Mouse hover must NOT drive this: pairing
  // onMouseEnter with "scroll cursor into view" creates a feedback loop
  // (scroll moves a new row under the pointer -> cursor jumps -> scroll
  // again) that makes the list thrash and hard to click.
  const [cursor, setCursor] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  /** Only true after ArrowUp/ArrowDown; gates the scroll-into-view effect. */
  const keyboardNavRef = useRef(false)

  useEffect(() => {
    if (!open) return
    setQuery("")
    setCursor(0)
    setOffline({})
    keyboardNavRef.current = false
    // Autofocus after paint, otherwise the dialog's own mount steals it back.
    const t = setTimeout(() => inputRef.current?.focus(), 0)
    return () => clearTimeout(t)
  }, [open])

  // Catalogue: server-side /llm/health lists models for every backend (CORS-
  // safe, works for OpenAI/Groq/vLLM). Local CLI bridges are also probed from
  // the browser so Grok/Claude can refresh live without waiting on the API.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setProbing(Object.fromEntries(backends.map((b) => [b.name, true])))

    getLlmHealth()
      .then((h) => {
        if (cancelled) return
        const models: Record<string, string[]> = {}
        const off: Record<string, boolean> = {}
        for (const b of h.backends) {
          if (b.available_models?.length) models[b.name] = b.available_models
          off[b.name] = b.status === "unhealthy"
        }
        setCliModels((c) => ({ ...models, ...c }))
        setOffline((o) => ({ ...off, ...o }))
      })
      .catch(() => {})
      .finally(() => {
        if (cancelled) return
        setProbing((p) => {
          const next = { ...p }
          for (const b of backends) {
            if (!bridgePort(b.base_url)) next[b.name] = false
          }
          return next
        })
      })

    const bridges = backends.filter((b) => bridgePort(b.base_url))
    bridges.forEach(async (b) => {
      const port = bridgePort(b.base_url)!
      try {
        const ctrl = new AbortController()
        const timer = setTimeout(() => ctrl.abort(), 2000)
        const res = await fetch(`http://127.0.0.1:${port}/v1/models`, { signal: ctrl.signal })
        clearTimeout(timer)
        if (!res.ok) throw new Error(String(res.status))
        const json = await res.json()
        const ids: string[] = (json?.data ?? [])
          .map((m: { id?: string }) => m.id)
          .filter((i: unknown): i is string => typeof i === "string" && i.length > 0)
        if (!cancelled && ids.length) setCliModels((c) => ({ ...c, [b.name]: ids }))
        if (!cancelled) setOffline((o) => ({ ...o, [b.name]: false }))
      } catch {
        // Local probe failed (CORS, bridge down). Keep the health-endpoint
        // status rather than flipping the group Offline over a browser CORS miss.
      } finally {
        if (!cancelled) setProbing((p) => ({ ...p, [b.name]: false }))
      }
    })
    return () => {
      cancelled = true
    }
  }, [open, backends])

  const groups: Group[] = useMemo(() => {
    const q = query.trim().toLowerCase()
    return backends
      .map((backend) => {
        const catalogue = cliModels[backend.name]
        const current = backend.effective_model || backend.model
        const models = catalogue?.length ? catalogue : [current]
        const rows = models
          .filter(
            (m) =>
              !q || m.toLowerCase().includes(q) || backend.name.toLowerCase().includes(q),
          )
          .map((model) => ({ backend, model }))
        return {
          backend,
          isBridge: !!bridgePort(backend.base_url),
          loading: !!probing[backend.name],
          offline: !!offline[backend.name],
          rows,
        }
      })
      .filter((g) => g.rows.length > 0)
  }, [backends, cliModels, probing, offline, query])

  // One flat index across every group, so the arrow keys walk the whole list
  // and never get trapped inside a section.
  const flat: Row[] = useMemo(() => groups.flatMap((g) => g.rows), [groups])

  // Pre-compute flat index per group+row so we don't mutate state during render.
  const flatByGroup: Map<string, number[]> = useMemo(() => {
    const m = new Map<string, number[]>()
    let idx = 0
    for (const g of groups) {
      m.set(g.backend.name, g.rows.map(() => idx++))
    }
    return m
  }, [groups])

  useEffect(() => {
    setCursor((c) => (flat.length === 0 ? 0 : Math.min(c, flat.length - 1)))
  }, [flat.length])

  // Scroll the keyboard cursor into view ONLY after arrow-key moves.
  // Using getBoundingClientRect (not offsetTop) because rows sit inside
  // per-backend wrappers, so offsetTop is relative to the group, not the
  // scroll container - which made scroll jumps overshoot and thrash.
  useEffect(() => {
    if (!keyboardNavRef.current) return
    keyboardNavRef.current = false
    const list = listRef.current
    const el = list?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)
    if (!list || !el) return
    const listRect = list.getBoundingClientRect()
    const elRect = el.getBoundingClientRect()
    if (elRect.top < listRect.top) {
      list.scrollTop -= listRect.top - elRect.top
    } else if (elRect.bottom > listRect.bottom) {
      list.scrollTop += elRect.bottom - listRect.bottom
    }
  }, [cursor])

  if (!open) return null

  const choose = (row: Row) => {
    onSelect(row.backend, row.model)
    onClose()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      if (!flat.length) return
      keyboardNavRef.current = true
      setCursor((c) => (c + 1) % flat.length)
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      if (!flat.length) return
      keyboardNavRef.current = true
      setCursor((c) => (c - 1 + flat.length) % flat.length)
    } else if (e.key === "Enter") {
      e.preventDefault()
      if (flat[cursor]) choose(flat[cursor])
    } else if (e.key === "Escape") {
      e.preventDefault()
      onClose()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-[12vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Select a model"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
        className="flex max-h-[70vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-surface bg-panel shadow-glow-strong"
      >
        <div className="flex items-center gap-2 border-b border-surface px-4">
          <Search className="h-4 w-4 shrink-0 text-cream-dim" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              keyboardNavRef.current = false
              setCursor(0)
            }}
            placeholder="Search models…"
            aria-label="Search models"
            className="min-w-0 flex-1 bg-transparent py-3.5 text-sm text-cream placeholder:text-cream-dim focus:outline-none"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div ref={listRef} className="reader-scroll min-h-0 flex-1 overflow-y-auto py-1 overscroll-contain">
          {groups.length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
              <Cpu className="h-5 w-5 text-cream-dim" />
              <p className="text-xs text-cream-dim">No model matches “{query}”</p>
            </div>
          ) : (
            groups.map((g) => (
              <div key={g.backend.name} className="pb-1">
                <div className="sticky top-0 z-10 flex items-center gap-2 bg-panel px-4 py-1.5">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-cream-dim">
                    {g.backend.name}
                  </span>
                  {g.isBridge && (
                    <span className="rounded-full border border-indigo/40 bg-indigo/10 px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-cream">
                      Local CLI
                    </span>
                  )}
                  {g.offline && !g.loading && (
                    <span className="rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-[#f87171]">
                      Offline
                    </span>
                  )}
                  {g.loading && <Loader2 className="h-3 w-3 animate-spin text-cream-dim" />}
                  <span className="ml-auto truncate text-[10px] text-cream-dim">
                    {endpointLabel(g.backend.base_url)}
                  </span>
                </div>
                {g.rows.map((row, ri) => {
                  const i = flatByGroup.get(g.backend.name)?.[ri] ?? -1
                  const selected =
                    row.backend.name === activeName &&
                    row.model === (row.backend.effective_model || row.backend.model)
                  return (
                    <button
                      key={`${row.backend.name}::${row.model}`}
                      data-idx={i}
                      type="button"
                      onClick={() => choose(row)}
                      aria-current={selected}
                      className={`flex w-full items-center gap-2.5 px-4 py-2 text-left transition-colors hover:bg-surface ${
                        i === cursor ? "bg-surface" : ""
                      }`}
                    >
                      <Check
                        className={`h-3.5 w-3.5 shrink-0 text-indigo ${selected ? "" : "invisible"}`}
                      />
                      <span
                        className={`min-w-0 flex-1 truncate text-[13px] ${g.offline ? "text-cream-dim" : "text-cream"}`}
                      >
                        {row.model}
                      </span>
                      {selected && (
                        <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-indigo">
                          Active
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-surface px-4 py-2 text-[10px] text-cream-dim">
          <span>
            <kbd className="rounded border border-surface px-1">↑</kbd>
            <kbd className="ml-0.5 rounded border border-surface px-1">↓</kbd> navigate
          </span>
          <span>
            <kbd className="rounded border border-surface px-1">↵</kbd> select
          </span>
          <span>
            <kbd className="rounded border border-surface px-1">esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  )
}
