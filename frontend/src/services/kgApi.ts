import api from "./api"

export interface KgEntity {
  id: number
  document_id: number
  name: string
  type: string
  description?: string | null
  pages_mentioned?: number[]
  created_at: string
}

export interface KgRelationship {
  id: number
  document_id: number
  subject: string
  relationship: string
  object: string
  page?: number | null
  created_at: string
}

export interface KgStatus {
  document_id: number
  status: "pending" | "extracting" | "completed" | "failed"
  chunks_processed: number
  total_chunks: number
  entity_count: number
  error?: string | null
}

export interface KgStats {
  entity_count: number
  relationship_count: number
  avg_connectivity: number
  total_pages_mentioned: number
  entity_types: Record<string, number>
}

export interface KgQueryResult {
  matched_entities: string[]
  context: string
  relationships: string[]
}

export const buildKg = (docId: number) =>
  api.post<{ status: string }>(`/api/documents/${docId}/kg/build`).then((r) => r.data)

export const getKgStatus = (docId: number) =>
  api.get<KgStatus>(`/api/documents/${docId}/kg/status`).then((r) => r.data)

export const getKgEntities = (docId: number, type?: string) => {
  const params = type ? `?entity_type=${encodeURIComponent(type)}` : ""
  return api.get<{ count: number; entities: KgEntity[] }>(`/api/documents/${docId}/kg/entities${params}`).then((r) => r.data)
}

export const getKgRelationships = (docId: number) =>
  api.get<{ count: number; relationships: KgRelationship[] }>(`/api/documents/${docId}/kg/relationships`).then((r) => r.data)

export const queryKg = (docId: number, query: string) =>
  api.post<KgQueryResult>(`/api/documents/${docId}/kg/query`, { query }).then((r) => r.data)

export const getKgStats = (docId: number) =>
  api.get<KgStats>(`/api/documents/${docId}/kg/stats`).then((r) => r.data)

export const deleteKg = (docId: number) =>
  api.delete<void>(`/api/documents/${docId}/kg`).then((r) => r.data)
