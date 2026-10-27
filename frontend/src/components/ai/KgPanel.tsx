"use client"

import { useEffect, useState } from "react"
import {
  KgStatus,
  KgStats,
  KgEntity,
  KgRelationship,
  buildKg,
  getKgStatus,
  getKgEntities,
  getKgRelationships,
  getKgStats,
  deleteKg,
} from "@/services/kgApi"
import { Loader2, Database, Network, Trash2, AlertCircle, CheckCircle, Sparkles } from "lucide-react"

interface KgPanelProps {
  docId: number
}

const STATUS_COLORS: Record<string, string> = {
  pending: "text-cream-dim",
  extracting: "text-indigo",
  completed: "text-emerald-400",
  failed: "text-red-400",
}

export default function KgPanel({ docId }: KgPanelProps) {
  const [status, setStatus] = useState<KgStatus | null>(null)
  const [stats, setStats] = useState<KgStats | null>(null)
  const [entities, setEntities] = useState<KgEntity[]>([])
  const [relationships, setRelationships] = useState<KgRelationship[]>([])
  const [building, setBuilding] = useState(false)
  const [tab, setTab] = useState<"status" | "entities" | "relationships">("status")
  const [entityFilter, setEntityFilter] = useState<string | undefined>()

  // Poll while a build is in flight. A build is "in flight" from the moment
  // Build KG is clicked (building) until the worker flips the state to a
  // terminal status. Polling only while status === "extracting" misses the
  // build entirely: the interval's first tick fires before the worker has
  // even set "extracting", sees "pending", and clears itself.
  const inFlight = building || status?.status === "extracting"

  useEffect(() => {
    getKgStatus(docId).then(setStatus).catch(() => {})
    getKgStats(docId).then(setStats).catch(() => {})
    if (!inFlight) return
    const interval = setInterval(() => {
      getKgStatus(docId).then((s: KgStatus) => {
        setStatus(s)
        if (s.status !== "extracting") {
          getKgStats(docId).then(setStats).catch(() => {})
        }
      }).catch(() => {})
    }, 3000)
    return () => clearInterval(interval)
  }, [docId, inFlight])

  useEffect(() => {
    if (tab === "entities") {
      getKgEntities(docId, entityFilter).then((r: { count: number; entities: KgEntity[] }) => setEntities(r.entities)).catch(() => {})
    }
    if (tab === "relationships") {
      getKgRelationships(docId).then((r: { count: number; relationships: KgRelationship[] }) => setRelationships(r.relationships)).catch(() => {})
    }
  }, [docId, tab, entityFilter])

  const handleBuild = async () => {
    setBuilding(true)
    try {
      await buildKg(docId)
      getKgStatus(docId).then(setStatus).catch(() => {})
    } catch {
      // Error handled by polling.
    } finally {
      setBuilding(false)
    }
  }

  const handleDelete = async () => {
    try {
      await deleteKg(docId)
      getKgStatus(docId).then(setStatus).catch(() => {})
      getKgStats(docId).then(setStats).catch(() => {})
      setEntities([])
      setRelationships([])
    } catch {
      // Silently fail.
    }
  }

  const entityTypes = stats?.entity_types
  const entityTypeList = entityTypes
    ? Object.entries(entityTypes).map(([k, v]) => [k, v as number] as [string, number]).sort((a, b) => b[1] - a[1])
    : []

  return (
    <div className="flex h-full flex-col">
      {/* Header with build/delete actions. */}
      <div className="border-b border-surface p-3">
        <div className="mb-2 flex items-center gap-2">
          <Database className="h-4 w-4 text-indigo" />
          <h4 className="text-xs font-bold text-cream">Knowledge Graph</h4>
          <span className={`ml-auto text-[10px] font-medium ${STATUS_COLORS[status?.status ?? "pending"]}`}>
            {status?.status ?? "pending"}
          </span>
        </div>

        {status?.status === "extracting" && (
          <div className="mb-2 flex items-center gap-2 text-[11px] text-cream-dim">
            <Loader2 className="h-3 w-3 animate-spin text-indigo" />
            Extracting {status.chunks_processed}/{status.total_chunks} chunks
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={handleBuild}
            disabled={building || status?.status === "extracting"}
            className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-indigo/40 bg-indigo/10 py-1.5 text-[11px] font-medium text-indigo transition-colors hover:bg-indigo/20 disabled:opacity-40"
          >
            {building ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
            {building ? "Building..." : "Build KG"}
          </button>
          {(status?.entity_count ?? 0) > 0 && (
            <button
              onClick={handleDelete}
              title="Delete knowledge graph"
              className="rounded-lg border border-surface p-1.5 text-cream-dim transition-colors hover:border-red-400/40 hover:text-red-400"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Tabs. */}
      <div className="border-b border-surface">
        <div className="flex" role="tablist">
          {([
            { t: "status" as const, icon: <Network className="h-3 w-3" key="icon" />, label: "Overview" as const },
            { t: "entities" as const, icon: <Database className="h-3 w-3" key="icon" />, label: "Entities" as const },
            { t: "relationships" as const, icon: <Sparkles className="h-3 w-3" key="icon" />, label: "Relations" as const },
          ]).map((item) => (
            <button
              key={item.t}
              role="tab"
              aria-selected={tab === item.t}
              onClick={() => setTab(item.t)}
              className={`flex flex-1 items-center justify-center gap-1 border-b-2 px-2 py-1.5 text-[11px] font-medium transition-colors ${
                tab === item.t
                  ? "border-indigo text-indigo"
                  : "border-transparent text-cream-dim hover:text-cream"
              }`}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content. */}
      <div className="flex-1 overflow-y-auto p-3">
        {tab === "status" && (
          <div className="space-y-3">
            {/* Stats grid. */}
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-surface bg-void/50 p-2">
                <div className="text-[10px] text-cream-dim">Entities</div>
                <div className="text-lg font-bold text-cream">{stats?.entity_count ?? 0}</div>
              </div>
              <div className="rounded-lg border border-surface bg-void/50 p-2">
                <div className="text-[10px] text-cream-dim">Relationships</div>
                <div className="text-lg font-bold text-cream">{stats?.relationship_count ?? 0}</div>
              </div>
              <div className="rounded-lg border border-surface bg-void/50 p-2">
                <div className="text-[10px] text-cream-dim">Avg Connectivity</div>
                <div className="text-lg font-bold text-cream">{stats?.avg_connectivity ?? 0}</div>
              </div>
              <div className="rounded-lg border border-surface bg-void/50 p-2">
                <div className="text-[10px] text-cream-dim">Pages Referenced</div>
                <div className="text-lg font-bold text-cream">{stats?.total_pages_mentioned ?? 0}</div>
              </div>
            </div>

            {/* Entity type breakdown. */}
            {entityTypeList.length > 0 && (
              <div>
                <div className="mb-1.5 text-[11px] font-medium text-cream-dim">Entity Types</div>
                <div className="space-y-1">
                  {entityTypeList.map(([type, count]) => (
                    <button
                      key={type}
                      onClick={() => {
                        setEntityFilter(entityFilter === type ? undefined : type)
                        setTab("entities")
                      }}
                      className="flex w-full items-center justify-between rounded-md bg-void/30 px-2 py-1 text-[11px] transition-colors hover:bg-void/60"
                    >
                      <span className="capitalize text-cream">{type}</span>
                      <span className="text-cream-dim">{count}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {status?.status === "failed" && status.error && (
              <div className="flex items-start gap-2 rounded-lg border border-red-400/20 bg-red-400/5 p-2 text-[11px] text-red-300">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{status.error}</span>
              </div>
            )}

            {status?.status === "completed" && (
              <div className="flex items-center gap-2 text-[11px] text-emerald-400">
                <CheckCircle className="h-3.5 w-3.5" />
                Extraction complete
              </div>
            )}

            {!status || status.status === "pending" ? (
              <div className="flex items-center gap-2 text-[11px] text-cream-dim">
                <Database className="h-3.5 w-3.5" />
                Click Build KG to extract entities and relationships
              </div>
            ) : null}
          </div>
        )}

        {tab === "entities" && (
          <div className="space-y-1">
            {entityFilter && (
              <button
                onClick={() => setEntityFilter(undefined)}
                className="mb-1 flex items-center gap-1 rounded-md bg-indigo/10 px-2 py-1 text-[10px] font-medium text-indigo"
              >
                <span className="capitalize">{entityFilter}</span>
                <span className="text-[9px] opacity-60">(click to clear)</span>
              </button>
            )}
            {entities.length === 0 ? (
              <div className="py-4 text-center text-[11px] text-cream-dim">
                No entities{entityFilter ? ` of type "${entityFilter}"` : ""} found
              </div>
            ) : (
              entities.map((e) => (
                <div
                  key={e.id}
                  className="rounded-md border border-surface/50 bg-void/30 p-2"
                >
                  <div className="flex items-start justify-between">
                    <div className="text-[12px] font-medium text-cream">{e.name}</div>
                    <div className="rounded bg-surface px-1.5 py-0.5 text-[9px] capitalize text-cream-dim">
                      {e.type}
                    </div>
                  </div>
                  {e.description && (
                    <div className="mt-1 text-[11px] leading-snug text-cream-dim">{e.description}</div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {tab === "relationships" && (
          <div className="space-y-1">
            {relationships.length === 0 ? (
              <div className="py-4 text-center text-[11px] text-cream-dim">
                No relationships found
              </div>
            ) : (
              relationships.map((r) => (
                <div
                  key={r.id}
                  className="rounded-md border border-surface/50 bg-void/30 px-2 py-1.5"
                >
                  <div className="text-[11px] leading-snug">
                    <span className="font-medium text-cream">{r.subject}</span>
                    <span className="mx-1 text-indigo">{r.relationship}</span>
                    <span className="font-medium text-cream">{r.object}</span>
                  </div>
                  {r.page && (
                    <div className="text-[9px] text-cream-dim">p. {r.page}</div>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
