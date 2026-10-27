import api from "./api"

// ---------------------------------------------------------------------------
// Types

export type CardType = "basic" | "reversed" | "cloze"
export type CardSource = "ai" | "manual" | "note"

// FSRS state: 1=Learning 2=Review 3=Relearning (matches fsrs.State)
export type FsrsState = 1 | 2 | 3

export interface Flashcard {
  id: number
  document_id: number
  card_type: "basic" | "cloze" // "reversed" only exists at creation time; each resulting row is "basic"
  question: string
  answer: string
  cloze_text?: string | null
  cloze_index?: number | null
  group_id?: number | null
  source: CardSource
  page_number?: number | null
  fsrs_state: FsrsState
  due: string
  last_review?: string | null
  reps: number
  lapses: number
  stability?: number | null
  difficulty?: number | null
  created_at: string
  updated_at?: string | null
}

// Rating buttons, in display order left-to-right (matches fsrs.Rating values)
export type Rating = 1 | 2 | 3 | 4
export const RATINGS: { value: Rating; label: string }[] = [
  { value: 1, label: "Again" },
  { value: 2, label: "Hard" },
  { value: 3, label: "Good" },
  { value: 4, label: "Easy" },
]

export interface FlashcardQueueItem extends Flashcard {
  preview_intervals: Partial<Record<"again" | "hard" | "good" | "easy", string>>
}

export interface FlashcardStats {
  due: number
  new: number
  total: number
  new_remaining_today: number
  review_remaining_today: number
}

export interface FlashcardSettings {
  daily_new_limit: number
  daily_review_limit: number
}

export interface FlashcardCreatePayload {
  document_id: number
  card_type: CardType
  question?: string
  answer?: string
  cloze_text?: string
  page_number?: number | null
}

export interface FlashcardUpdatePayload {
  question?: string
  answer?: string
  cloze_text?: string
  page_number?: number | null
}

// ---------------------------------------------------------------------------
// Calls

export const generateFlashcards = (documentId: number) =>
  api.post("/api/flashcards/generate", { document_id: documentId }).then((r) => r.data)

export const getFlashcards = (documentId: number) =>
  api.get<Flashcard[]>(`/api/flashcards?document_id=${documentId}`).then((r) => r.data)

export const createFlashcard = (payload: FlashcardCreatePayload) =>
  api.post<Flashcard[]>("/api/flashcards", payload).then((r) => r.data)

export const updateFlashcard = (id: number, payload: FlashcardUpdatePayload) =>
  api.put<Flashcard>(`/api/flashcards/${id}`, payload).then((r) => r.data)

export const deleteFlashcard = (id: number, group = false) =>
  api.delete(`/api/flashcards/${id}${group ? "?group=true" : ""}`).then((r) => r.data)

export const getFlashcardQueue = (documentId: number, limit = 200) =>
  api
    .get<FlashcardQueueItem[]>(`/api/flashcards/queue?document_id=${documentId}&limit=${limit}`)
    .then((r) => r.data)

export const reviewFlashcard = (id: number, rating: Rating, durationMs?: number) =>
  api
    .post<Flashcard>(`/api/flashcards/${id}/review`, { rating, duration_ms: durationMs ?? null })
    .then((r) => r.data)

export const getFlashcardStats = (documentId: number) =>
  api.get<FlashcardStats>(`/api/flashcards/stats?document_id=${documentId}`).then((r) => r.data)

export const getFlashcardSettings = () =>
  api.get<FlashcardSettings>("/api/flashcards/settings").then((r) => r.data)

export const updateFlashcardSettings = (settings: FlashcardSettings) =>
  api.put<FlashcardSettings>("/api/flashcards/settings", settings).then((r) => r.data)
