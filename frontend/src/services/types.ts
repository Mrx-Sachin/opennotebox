

export type LayoutMode = "auto" | "single_column" | "research_paper"

export interface Document {
  id: number
  user_id: number
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
}

export interface ExtractedPage {
  id: number
  document_id: number
  page_number: number
  content: string
  bounding_boxes?: any
  headings?: any
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

export interface Citation {
  n: number
  doc_id: number
  doc_title: string
  /** Page number, or null for non-paged sources (e.g. knowledge graph). */
  page: number | null
  excerpt: string
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
