import { refreshToken } from "@/services/api"
import { apiUrl } from "@/lib/apiBase"
import { Message } from "@/services/types"
import { RagMode } from "@/services/api"

type StreamEvent =
  | { type: "status"; stage: string }
  | { type: "token"; content: string }
  | { type: "final"; message: Message }
  | { type: "error"; detail: string }

interface StreamHandlers {
  onToken: (content: string) => void
  onStatus?: (stage: string) => void
}

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const storedRefresh = localStorage.getItem("refresh_token")
  if (!storedRefresh) return null
  if (!refreshPromise) {
    refreshPromise = refreshToken(storedRefresh)
      .then((data) => {
        localStorage.setItem("token", data.access_token)
        localStorage.setItem("refresh_token", data.refresh_token)
        return data.access_token as string
      })
      .catch(() => null)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

async function postStream(
  sessionId: number,
  body: Record<string, unknown>,
  signal: AbortSignal,
): Promise<Response> {
  const send = (token: string | null) =>
    fetch(apiUrl(`/api/chat/sessions/${sessionId}/messages/stream`), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    })

  let response = await send(localStorage.getItem("token"))
  if (response.status === 401 && localStorage.getItem("refresh_token")) {
    const token = await refreshAccessToken()
    if (token) response = await send(token)
  }
  return response
}

function decodeEvent(block: string): StreamEvent | null {
  const data = block
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n")
  if (!data) return null
  return JSON.parse(data) as StreamEvent
}

async function* readEvents(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) throw new Error("The chat response did not include a stream.")
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let pending = ""
  try {
    while (true) {
      const { value, done } = await reader.read()
      pending += decoder.decode(value, { stream: !done })
      let separator = /\r\n\r\n|\n\n|\r\r/.exec(pending)
      while (separator) {
        const block = pending.slice(0, separator.index)
        pending = pending.slice(separator.index + separator[0].length)
        const event = decodeEvent(block)
        if (event) yield event
        separator = /\r\n\r\n|\n\n|\r\r/.exec(pending)
      }
      if (done) break
    }
    if (pending.trim()) {
      const event = decodeEvent(pending)
      if (event) yield event
    }
  } finally {
    reader.releaseLock()
  }
}

export async function streamMessage(
  sessionId: number,
  content: string,
  documentIds: number[],
  ragMode: RagMode | undefined,
  signal: AbortSignal,
  handlers: StreamHandlers,
): Promise<Message> {
  const response = await postStream(
    sessionId,
    {
      content,
      document_ids: documentIds,
      ...(ragMode ? { rag_mode: ragMode } : {}),
    },
    signal,
  )

  if (!response.ok) {
    let detail = `Chat request failed (${response.status}).`
    try {
      const error = await response.json()
      if (typeof error.detail === "string") detail = error.detail
    } catch {
      // Keep the status text when an intermediary returned non-JSON content.
    }
    throw new Error(detail)
  }

  let finalMessage: Message | null = null
  for await (const event of readEvents(response)) {
    if (event.type === "status") handlers.onStatus?.(event.stage)
    if (event.type === "token") handlers.onToken(event.content)
    if (event.type === "error") throw new Error(event.detail)
    if (event.type === "final") finalMessage = event.message
  }
  if (!finalMessage) {
    throw new Error("The chat response ended before the answer was saved.")
  }
  return finalMessage
}
