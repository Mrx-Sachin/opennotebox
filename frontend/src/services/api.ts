import axios, { AxiosError, InternalAxiosRequestConfig } from "axios"
import {
  Document,
  DocumentLayoutStatus,
  ExtractedPage,
  ExtractedPageLightweight,
  Summary,
  Message,
  Conversation,
  User,
  LayoutMode,
  Note,
  NoteColor,
  NoteCreate,
  NoteUpdate,
} from "./types"
export type {
  Document,
  ExtractedPage,
  ExtractedPageLightweight,
  Summary,
  Message,
  Conversation,
  User,
  LayoutMode,
  Note,
  NoteColor,
  NoteCreate,
  NoteUpdate,
}
import { API_BASE, apiUrl } from "@/lib/apiBase"

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token")
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Auth
export const login = (username: string, password: string) =>
  api.post("/api/auth/login", { username, password }).then((r) => r.data)

export const register = (username: string, password: string) =>
  api.post("/api/auth/register", { username, password }).then((r) => r.data)

export const refreshToken = (refreshToken: string) =>
  api.post("/api/auth/refresh", { refresh_token: refreshToken }).then((r) => r.data)

export const getMe = () =>
  api.get<User>("/api/auth/me").then((r) => r.data)

export const getDocumentLayout = (docId: number, signal?: AbortSignal) =>
  api.get<DocumentLayoutStatus>(`/api/documents/${docId}/layout`, { signal }).then((r) => r.data)

export const analyzeDocumentLayout = (docId: number) =>
  api.post<DocumentLayoutStatus>(`/api/documents/${docId}/analyze-layout`).then((r) => r.data)

// Admin endpoints
export const listUsers = () =>
  api.get<User[]>("/api/auth/users").then((r) => r.data)

export interface AdminDocInfo {
  id: number
  title: string
  file_size: number
  num_pages: number
  format: string
  processing_status: string
  created_at: string | null
}

export interface UserStats {
  id: number
  username: string
  is_admin: boolean
  created_at: string | null
  doc_count: number
  total_bytes: number
  documents: AdminDocInfo[]
}

export const getUserStats = () =>
  api.get<UserStats[]>("/api/auth/users/stats").then((r) => r.data)

export const updateUser = (userId: number, isAdmin: boolean) =>
  api.patch<User>(`/api/auth/users/${userId}`, { is_admin: isAdmin }).then((r) => r.data)

export const deleteUser = (userId: number) =>
  api.delete(`/api/auth/users/${userId}`).then((r) => r.data)

export const adminCreateUser = (username: string, password: string) =>
  api.post<User>("/api/auth/users", { username, password }).then((r) => r.data)

// --- 401 handling -----------------------------------------------------
// The access token expires (24h) long before the user's session ends, and
// nothing was calling refreshToken() - every request past expiry silently
// 401'd and the app just looked broken. This interceptor makes one refresh
// attempt on 401, retries the original request, and otherwise sends the
// user back to /login (preserving where they were, via ?next=).
//
// A single expiring token can fail many in-flight requests at once (the
// dashboard polls documents every 5s, chat/tts fire their own calls, etc).
// Without coordination each of those would race to refresh independently,
// burning the refresh token's single-use-ish window and causing spurious
// extra logouts. `refreshPromise` makes every 401 that arrives while a
// refresh is already underway await that SAME promise instead of starting
// its own - one refresh call, N retried requests.
let refreshPromise: Promise<string | null> | null = null

const AUTH_ENDPOINT_RE = /\/api\/auth\/(login|register|refresh)(\?|$)/

function isAuthEndpoint(url?: string): boolean {
  return !!url && AUTH_ENDPOINT_RE.test(url)
}

function sendToLogin() {
  localStorage.removeItem("token")
  localStorage.removeItem("refresh_token")
  if (typeof window === "undefined") return
  if (window.location.pathname === "/login") return
  const next = window.location.pathname + window.location.search
  window.location.href = `/login?next=${encodeURIComponent(next)}`
}

type RetriableConfig = InternalAxiosRequestConfig & { _retried401?: boolean }

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined

    // Don't try to refresh-and-retry on the auth endpoints themselves -
    // a 401 from /login or /refresh means the credentials/token are bad,
    // not that the access token expired, so retrying would just loop.
    if (!config || error.response?.status !== 401 || isAuthEndpoint(config.url)) {
      return Promise.reject(error)
    }
    if (config._retried401) {
      // The retried request 401'd again with a supposedly-fresh token:
      // the session is unrecoverable, stop looping and bounce to login.
      sendToLogin()
      return Promise.reject(error)
    }
    config._retried401 = true

    const storedRefresh = localStorage.getItem("refresh_token")
    if (!storedRefresh) {
      sendToLogin()
      return Promise.reject(error)
    }

    if (!refreshPromise) {
      refreshPromise = refreshToken(storedRefresh)
        .then((data) => {
          localStorage.setItem("token", data.access_token)
          localStorage.setItem("refresh_token", data.refresh_token)
          return data.access_token as string
        })
        .catch(() => {
          sendToLogin()
          return null
        })
        .finally(() => {
          refreshPromise = null
        })
    }

    const newToken = await refreshPromise
    if (!newToken) {
      return Promise.reject(error)
    }
    config.headers.Authorization = `Bearer ${newToken}`
    return api(config)
  }
)

// Documents
export const getDocuments = () =>
  api.get<Document[]>("/api/documents").then((r) => r.data)

export const getDocument = (id: number) =>
  api.get<Document>(`/api/documents/${id}`).then((r) => r.data)

export const getDocumentStatus = (id: number) =>
  api.get(`/api/documents/${id}/status`).then((r) => r.data)

export const uploadFile = (
  file: File,
  onProgress?: (percent: number) => void,
  layoutMode: LayoutMode = "auto"
) => {
  const formData = new FormData()
  formData.append("file", file)
  formData.append("layout_mode", layoutMode)
  return api
    .post<Document>("/api/documents/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    })
    .then((r) => r.data)
}

/** Re-parse a PDF with an optional new layout_mode (column detection). */
export const reparseDocument = (docId: number, layoutMode?: LayoutMode) =>
  api
    .post<{ doc_id: number; status: string; layout_mode: string }>(
      `/api/documents/${docId}/re-parse`,
      layoutMode ? { layout_mode: layoutMode } : {}
    )
    .then((r) => r.data)

export const uploadFromUrl = (url: string, notebookId?: number | null) => {
  const formData = new FormData()
  formData.append("url", url)
  if (notebookId != null) formData.append("notebook_id", String(notebookId))
  return api.post("/api/documents/upload-url", formData).then((r) => r.data)
}

/** Record where the reader is. Fire-and-forget by design: losing one of
    these costs at most a few pages of accuracy, and it must never surface an
    error over the document the user is reading. */
export const updateReadingProgress = (
  docId: number,
  page: number,
  position?: number,
) =>
  api
    .put<Document>(`/api/documents/${docId}/progress`, { page, position }, {
      adapter: "fetch",
      fetchOptions: { keepalive: true },
    })
    .then((r) => r.data)

export const deleteDocument = (id: number) =>
  api.delete(`/api/documents/${id}`).then((r) => r.data)

export interface GetPagesOptions {
  lightweight?: boolean
}

// Pages
export function getPages(
  docId: number,
  options: { lightweight: true }
): Promise<ExtractedPageLightweight[]>
export function getPages(
  docId: number,
  options?: { lightweight?: false }
): Promise<ExtractedPage[]>
export function getPages(
  docId: number,
  options: GetPagesOptions
): Promise<ExtractedPageLightweight[]>
export function getPages(
  docId: number,
  options?: GetPagesOptions
): Promise<ExtractedPageLightweight[] | ExtractedPage[]> {
  const params = new URLSearchParams()
  if (options?.lightweight) params.set("lightweight", "true")
  const qs = params.toString() ? `?${params.toString()}` : ""
  return api.get<ExtractedPageLightweight[]>(`/api/documents/${docId}/pages${qs}`).then((r) => r.data)
}

export const getPage = (docId: number, pageNum: number) =>
  api.get<ExtractedPage>(`/api/documents/${docId}/page/${pageNum}`).then((r) => r.data)

// Summaries
export const getSummaries = (docId: number, mode?: string) => {
  const params = mode ? `?mode=${mode}` : ""
  return api.get<Summary[]>(`/api/documents/${docId}/summaries${params}`).then((r) => r.data)
}

// Chat
export const createConversation = () =>
  api.post<Conversation>("/api/chat/sessions").then((r) => r.data)

export const getConversations = () =>
  api.get<Conversation[]>("/api/chat/sessions").then((r) => r.data)

export type RagMode = "standard" | "hybrid" | "graph"

export const sendMessage = (
  sessionId: number,
  content: string,
  documentIds?: number[],
  signal?: AbortSignal,
  ragMode?: RagMode
) =>
  api
    .post<Message>(
      `/api/chat/sessions/${sessionId}/messages`,
      { content, document_ids: documentIds, rag_mode: ragMode },
      {
        // Retrieval + two LLM calls on a single GPU with 27-31B models
        // can take 5-10 min, so give it headroom on constrained hardware.
        timeout: 600000,
        // Aborting this closes the underlying connection, which is what
        // lets the backend's request.is_disconnected() poll (routers/chat.py)
        // actually notice and cancel the in-flight LLM call server-side -
        // not just hide the spinner while the GPU keeps working unseen.
        signal,
      }
    )
    .then((r) => r.data)

export const getMessages = (sessionId: number) =>
  api.get<Message[]>(`/api/chat/sessions/${sessionId}/messages`).then((r) => r.data)

export const deleteConversation = (sessionId: number) =>
  api.delete(`/api/chat/sessions/${sessionId}`).then((r) => r.data)

// TTS
export interface TtsPlayResponse {
  status: "cached" | "generating"
  text_hash: string
  cache_id?: number
  audio_url?: string
  duration?: number
}

export interface TtsCacheStatus {
  status: "ready" | "generating"
  cache_id?: number
  audio_url?: string
  duration?: number
}

export const playSummary = (summaryId: number) =>
  api.post<TtsPlayResponse>(`/api/tts/play/${summaryId}`).then((r) => r.data)

export const playPage = (docId: number, pageNum: number) =>
  api.post<TtsPlayResponse>(`/api/tts/play/page/${docId}/${pageNum}`).then((r) => r.data)

/** Synthesize arbitrary text (used for chunked gapless page reading).
    speed is the NATIVE synthesis speed (engine-clamped server-side), not
    the user's full playback rate - see ttsEngine's SPEED_NATIVE_CAP. */
export const playText = (text: string, voice?: string, speed?: number) =>
  api.post<TtsPlayResponse>("/api/tts/play/text", { text, voice, speed }).then((r) => r.data)

export const getTtsCacheStatus = (textHash: string) =>
  api.get<TtsCacheStatus>(`/api/tts/cache/${textHash}`).then((r) => r.data)

export interface TtsStreamResult {
  blobUrl: string
  duration: number
  cacheId: number | null
  cached: boolean
}

/** Stream-synthesize text directly - no queue-and-poll round trip. The
    backend returns WAV bytes as soon as they're ready (streamed on a cache
    miss, effectively instant on a hit), with the metadata a blob body can't
    carry - duration, cache id, hit/miss - riding along in headers instead.
    Takes an AbortSignal so a caller can cut the request off mid-flight:
    ttsEngine uses this for real cancellation when the user clicks a new
    line before an in-flight synthesis finishes, instead of the old
    poll loop that had no way to stop once started. */
export interface ReleaseAudioPayload {
  sessionId?: string
  cacheIds?: number[]
  allSession?: boolean
}

export interface ReleaseAudioResponse {
  status: string
  released_count: number
  deleted_count: number
}

export const heartbeatReadingAudio = (
  sessionId: string,
  signal?: AbortSignal
): Promise<{ status: string; renewed: boolean }> =>
  api
    .post<{ status: string; renewed: boolean }>(
      "/api/tts/heartbeat",
      { session_id: sessionId },
      { signal }
    )
    .then((response) => response.data)

export const releaseReadingAudio = (
  payload: ReleaseAudioPayload,
  signal?: AbortSignal
): Promise<ReleaseAudioResponse> =>
  api
    .post<ReleaseAudioResponse>(
      "/api/tts/release",
      {
        session_id: payload.sessionId,
        cache_ids: payload.cacheIds,
        all_session: payload.allSession,
      },
      { signal }
    )
    .then((response) => response.data)

export const beaconReleaseReadingAudio = (payload: ReleaseAudioPayload): boolean => {
  if (typeof window === "undefined" || typeof fetch === "undefined") return false
  let authToken: string | null = null
  try {
    authToken = localStorage.getItem("token")
  } catch (storageError) {
  }
  const requestBody = JSON.stringify({
    session_id: payload.sessionId,
    cache_ids: payload.cacheIds,
    all_session: payload.allSession,
  })
  const endpointUrl = apiUrl("/api/tts/release")
  try {
    fetch(endpointUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: requestBody,
      keepalive: true,
    }).catch(() => {})
    return true
  } catch (fetchError) {
    return false
  }
}

export const streamTts = (
  text: string,
  voice: string | null,
  speed: number | null,
  signal?: AbortSignal,
  sessionId?: string
): Promise<TtsStreamResult> =>
  api
    .post(
      "/api/tts/stream",
      { text, voice, speed, session_id: sessionId },
      { responseType: "blob", signal, timeout: 120000 }
    )
    .then((response) => {
      const durationHeader = parseFloat(response.headers["x-tts-duration"])
      const cacheIdHeader = response.headers["x-tts-cache-id"]
      return {
        blobUrl: URL.createObjectURL(response.data),
        duration: Number.isFinite(durationHeader) ? durationHeader : 0,
        cacheId: cacheIdHeader != null ? parseInt(cacheIdHeader, 10) : null,
        cached: response.headers["x-tts-cached"] === "hit",
      }
    })

export const getAudioUrl = (cacheId: number, sessionId?: string) =>
  apiUrl(`/api/tts/audio/${cacheId}${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`)

/** Fetch an audio endpoint with auth and return a playable object URL.
    <audio> elements can't send Authorization headers, so stream via axios. */
export const fetchAudioBlobUrl = (url: string) =>
  api
    .get(url, { responseType: "blob", timeout: 120000 })
    .then((r) => URL.createObjectURL(r.data))

export const getFileUrl = (docId: number) => apiUrl(`/api/documents/${docId}/file`)

// Podcasts
export interface PodcastLine {
  speaker: string
  text: string
  start_time?: number
  end_time?: number
  duration?: number
}

export interface PodcastInterruptResponse {
  interrupt_id: string
  audio_url: string
  duration: number
  lines: PodcastLine[]
  current_line?: PodcastLine | null
  next_line?: PodcastLine | null
  resume_time: number
  citations?: { page?: number; text?: string }[] | null
}

export interface PodcastTranscribeResponse {
  transcript: string
}

export interface Podcast {
  id: number
  document_id: number
  scope: "full" | "section"
  section_title?: string | null
  status: "generating" | "completed" | "failed"
  error?: string | null
  duration?: number | null
  transcript?: {
    title: string
    version?: number
    lines: PodcastLine[]
    scratchpad?: string | null
  } | null
  created_at: string
  audio_url?: string | null
  document_title?: string | null
}

export type PodcastLength = "short" | "medium" | "long" | "deep_dive"

export const createPodcast = (
  documentId: number,
  scope: "full" | "section",
  sectionTitle?: string,
  length: PodcastLength = "medium"
) =>
  api
    .post<Podcast>("/api/podcasts", {
      document_id: documentId,
      scope,
      section_title: sectionTitle ?? null,
      length,
    })
    .then((r) => r.data)

export const getPodcasts = (documentId?: number) => {
  const url = documentId !== undefined ? `/api/podcasts?document_id=${documentId}` : "/api/podcasts"
  return api.get<Podcast[]>(url).then((r) => r.data)
}

export const getPodcast = (id: number) =>
  api.get<Podcast>(`/api/podcasts/${id}`).then((r) => r.data)

export const deletePodcast = (id: number) =>
  api.delete(`/api/podcasts/${id}`).then((r) => r.data)

export const getPodcastAudioUrl = (id: number) => apiUrl(`/api/podcasts/${id}/audio`)

export const interruptPodcast = (
  podcastId: number,
  data: {
    question: string
    current_time: number
    line_index?: number | null
    history?: { speaker: string; text: string }[] | null
  }
) =>
  api
    .post<PodcastInterruptResponse>(`/api/podcasts/${podcastId}/interrupt`, data, { timeout: 120000 })
    .then((r) => r.data)

export const transcribeChatAudio = (
  audioBlob: Blob,
  filename = "chat_audio.wav"
) => {
  const formData = new FormData()
  formData.append("file", audioBlob, filename)
  return api
    .post<{ transcript: string }>("/api/chat/transcribe", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000,
    })
    .then((r) => r.data)
}

export const transcribePodcastAudio = (
  podcastId: number,
  audioBlob: Blob,
  filename = "question.wav"
) => {
  const formData = new FormData()
  formData.append("file", audioBlob, filename)
  return api
    .post<PodcastTranscribeResponse>(`/api/podcasts/${podcastId}/transcribe`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000,
    })
    .then((r) => r.data)
}

// Bookmarks
export interface Bookmark {
  id: number
  document_id: number
  page_number: number
  position?: number | null
  label?: string | null
  created_at: string
}

export const createBookmark = (documentId: number, pageNumber: number, position?: number, label?: string) =>
  api
    .post<Bookmark>("/api/bookmarks", {
      document_id: documentId,
      page_number: pageNumber,
      position: position ?? null,
      label: label ?? null,
    })
    .then((r) => r.data)

export const getBookmarks = (documentId: number) =>
  api.get<Bookmark[]>(`/api/bookmarks?document_id=${documentId}`).then((r) => r.data)

export const updateBookmark = (id: number, label: string) =>
  api.patch<Bookmark>(`/api/bookmarks/${id}`, { label }).then((r) => r.data)

export const deleteBookmark = (id: number) =>
  api.delete(`/api/bookmarks/${id}`).then((r) => r.data)

// Notes & highlights
export const getNotes = (documentId?: number, opts?: { q?: string; tag?: string }) => {
  const params = new URLSearchParams()
  if (documentId != null) params.set("document_id", String(documentId))
  if (opts?.q) params.set("q", opts.q)
  if (opts?.tag) params.set("tag", opts.tag)
  return api.get<Note[]>(`/api/notes?${params.toString()}`).then((r) => r.data)
}

export const createNote = (body: NoteCreate) =>
  api.post<Note>("/api/notes", body).then((r) => r.data)

export const updateNote = (id: number, body: NoteUpdate) =>
  api.patch<Note>(`/api/notes/${id}`, body).then((r) => r.data)

export const deleteNote = (id: number) =>
  api.delete(`/api/notes/${id}`).then((r) => r.data)

export const exportNotesMarkdown = (documentId?: number) => {
  const qs = documentId != null ? `?document_id=${documentId}` : ""
  return api
    .get<string>(`/api/notes/export${qs}`, { responseType: "text" })
    .then((r) => r.data)
}

export const makeNoteFlashcard = (noteId: number) =>
  api.post<{ id: number }>(`/api/notes/${noteId}/flashcard`, null, { timeout: 90000 }).then((r) => r.data)

export const getNoteColors = () =>
  api.get<NoteColor[]>("/api/notes/colors").then((r) => r.data)

export const createNoteColor = (name: string, hex: string, hint?: string) =>
  api.post<NoteColor>("/api/notes/colors", { name, hex, hint }).then((r) => r.data)

export const updateNoteColor = (id: string, body: { name?: string; hex?: string; hint?: string | null }) =>
  api.patch<NoteColor>(`/api/notes/colors/${id}`, body).then((r) => r.data)

export const deleteNoteColor = (id: string) =>
  api.delete(`/api/notes/colors/${id}`).then((r) => r.data)

// On-demand section summaries
export const generateSectionSummary = (docId: number, sectionTitle: string) =>
  api
    .post<Summary>(`/api/documents/${docId}/summaries/section`, { section_title: sectionTitle })
    .then((r) => r.data)

// Settings
export interface LlmBackend {
  name: string
  base_url: string
  /** The deployment-wide registry model, shared by every user. Round-trip
   * this unchanged when PUTting `backends`. */
  model: string
  /** What the current user actually runs on this backend: `model` with their
   * personal override applied. Read-only - change it by sending
   * `llm.model_overrides`, never by writing `model`. */
  effective_model?: string
  api_key_set?: boolean
  api_key?: string
}

export interface Settings {
  llm: {
    backends: LlmBackend[]
    active: string
  }
  tts: {
    default_voice: string
  }
  export: {
    enabled: boolean
  }
}

export const getSettings = () =>
  api.get<Settings>("/api/settings").then((r) => r.data)

/** Per-backend reachability, probed server-side (from the API container),
 * so "healthy" means a chat request can actually reach the endpoint. */
export interface LlmBackendHealth {
  name: string
  status: "healthy" | "degraded" | "unhealthy"
  latency_ms?: number | null
  available_models?: string[] | null
  error?: string | null
  http_status?: number | null
}

export interface LlmHealth {
  active: string
  backends: LlmBackendHealth[]
}

export const getLlmHealth = () =>
  api.get<LlmHealth>("/api/settings/llm/health").then((r) => r.data)

export interface LlmProbeResult {
  status: string
  latency_ms?: number | null
  available_models?: string[] | null
  error?: string | null
  http_status?: number | null
}

export const probeLlm = (baseUrl: string, apiKey?: string) =>
  api
    .post<LlmProbeResult>("/api/settings/llm/probe", {
      base_url: baseUrl,
      ...(apiKey ? { api_key: apiKey } : {}),
    })
    .then((r) => r.data)

export const updateSettings = (patch: {
  llm?: {
    active?: string
    backends?: LlmBackend[]
    /** {backend name: model id} for this user only. Merged server-side, so
     * send just the one you are changing; null clears an override. */
    model_overrides?: Record<string, string | null>
  }
  tts?: { default_voice?: string }
  export?: { enabled: boolean }
}) => api.put<Settings>("/api/settings", patch).then((r) => r.data)

// "Export to folder" mirror (chat/flashcards/podcasts written to disk under
// the deployment's EXPORT_DIR, in addition to staying in Postgres). The
// "enabled" toggle above lives in Settings; this triggers an immediate
// on-demand sync regardless of that toggle - see routers/export.py.
export interface ExportNowResult {
  status: string
  conversations_exported?: number
  flashcard_files_written?: number
  podcasts_exported?: number
  skipped?: string
  error?: string
}

export const exportNow = () =>
  api.post<ExportNowResult>("/api/export/now").then((r) => r.data)

export interface TtsVoice {
  id: string
  label: string
  description: string
}

export const getTtsVoices = () =>
  api.get<{ voices: TtsVoice[] }>("/api/tts/voices").then((r) => r.data)

/** Signal the TTS service to release its GPU session (Kokoro) and free VRAM.
    Best-effort fire-and-forget - doesn't wait for a response so it won't
    block page unload or tab close. */
export const releaseTtsGpu = () =>
  api.post<void>("/api/tts/release-gpu").catch(() => {})

// On-demand page summaries
export const generatePageSummary = (docId: number, pageNumber: number) =>
  api
    .post<Summary>(`/api/documents/${docId}/summaries/page`, { page_number: pageNumber })
    .then((r) => r.data)

export const generateAllPageSummaries = (docId: number) =>
  api.post<{ doc_id: number; status: string }>(`/api/documents/${docId}/summaries/pages/all`).then((r) => r.data)

export default api
