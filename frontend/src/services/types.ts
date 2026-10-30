

export type LayoutMode = "auto" | "single_column" | "research_paper"

export interface Document {
  id: number
  user_id: number
  notebook_id?: number | null
  title: string
  format: string
  file_size: number
  num_pages: number
  processing_status: string
  original_url?: string
  layout_mode?: LayoutMode
  created_at: string
  /** Where the reader was last left. Null means never opened, which is
      deliberately distinct from "opened at page 1". */
  last_read_page?: number | null
  last_read_position?: number | null
  last_read_at?: string | null
  can_share?: boolean
  is_shared?: boolean
  read_only?: boolean
  shared_title?: string | null
  author?: string | null
  description?: string | null
  topics?: string[]
  document_type?: string | null
  language?: string | null
}

export interface SharedDocument {
  id: number
  title: string
  author: string | null
  description: string | null
  topics: string[]
  document_type: string
  language: string | null
  num_pages: number
  file_size: number
  shared_at: string
  added: boolean
  reason?: string
}

export interface SharedLibraryResult {
  documents: SharedDocument[]
  total: number
  topics: string[]
  document_types: string[]
}

export interface SharedDocumentUpdate {
  is_shared: boolean
  title?: string
  author?: string
  description?: string
  topics?: string[]
  document_type?: string
  language?: string
}

export interface LayoutRegion {
  label: string
  narrate: boolean
  bbox: [number, number, number, number]
  source: "docling"
  text?: string
  start?: number
  end?: number
}

export interface NarrationExclusion {
  start: number
  end: number
}

export type DoclingRegion = LayoutRegion

export interface LegacyBoundingBox {
  text?: string
  bbox?: number[]
  source?: string
  narrate?: boolean
}

export type BoundingBoxItem = LayoutRegion | LegacyBoundingBox

export function isDoclingRegion(item: unknown): item is LayoutRegion {
  if (item == null || typeof item !== "object") return false
  const candidate = item as Record<string, unknown>
  return (
    candidate.source === "docling" &&
    typeof candidate.label === "string" &&
    typeof candidate.narrate === "boolean" &&
    Array.isArray(candidate.bbox) &&
    candidate.bbox.length === 4 &&
    candidate.bbox.every(
      (coord) => typeof coord === "number" && Number.isFinite(coord)
    )
  )
}

export function hasDoclingRegions(boxes: unknown): boolean {
  return Array.isArray(boxes) && boxes.some(isDoclingRegion)
}

export function extractDoclingRegions(boxes: unknown): LayoutRegion[] {
  if (!Array.isArray(boxes)) return []
  return boxes.filter(isDoclingRegion)
}

export interface ExtractedPage {
  id: number
  document_id: number
  page_number: number
  content: string
  bounding_boxes?: BoundingBoxItem[] | null
  headings?: any
  layout_regions?: LayoutRegion[] | null
  narration_exclusions?: NarrationExclusion[] | null
}

export interface Summary {
  id: number
  document_id: number
  mode: string
  page_number?: number
  section_title?: string
  content: string
  ai_model?: string
  created_at: string
  start_page?: number
  end_page?: number
  status?: "generating" | "failed" | null
}

export interface CitationEvidence {
  exact: string
}

export interface Citation {
  n: number
  doc_id: number
  doc_title: string
  /** Page number, or null for non-paged sources (e.g. knowledge graph). */
  page: number | null
  excerpt: string
  evidence?: CitationEvidence | null
}

export interface Message {
  id: number
  conversation_id: number
  role: string
  content: string
  citations?: Citation[]
  token_count: number
}

export interface Conversation {
  id: number
  user_id: number
  title: string
  document_ids?: number[]
  page_scope?: any
  created_at: string
}

export interface User {
  id: number
  username: string
  is_admin: boolean
  created_at: string
}

export interface Note {
  id: number
  document_id: number
  page_number: number
  end_page?: number | null
  quote: string | null
  context_before: string | null
  context_after: string | null
  note: string | null
  color: string
  word_range: [number, number] | null
  tags: string[]
  flashcard_id?: number | null
  document_title?: string | null
  created_at: string
  updated_at: string | null
}

export interface NoteColor {
  id: string
  name: string
  hex: string
  built_in: boolean
  hint?: string | null
}

export interface NoteCreate {
  document_id: number
  page_number: number
  end_page?: number | null
  quote?: string | null
  context_before?: string | null
  context_after?: string | null
  note?: string | null
  color?: string
  word_range?: [number, number] | null
  tags?: string[]
}

export interface NoteUpdate {
  note?: string | null
  color?: string
  tags?: string[]
}
