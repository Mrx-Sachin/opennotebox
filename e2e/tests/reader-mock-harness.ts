import fs from "fs"
import path from "path"
import { execFileSync } from "child_process"
import { expect, type Page } from "@playwright/test"

export function createSilentWav(seconds = 30): Buffer {
  const sampleRate = 8000
  const dataSize = sampleRate * seconds * 2
  const buffer = Buffer.alloc(44 + dataSize)
  buffer.write("RIFF", 0)
  buffer.writeUInt32LE(36 + dataSize, 4)
  buffer.write("WAVE", 8)
  buffer.write("fmt ", 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(sampleRate, 24)
  buffer.writeUInt32LE(sampleRate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write("data", 36)
  buffer.writeUInt32LE(dataSize, 40)
  return buffer
}

export const SILENT_WAV_BUFFER = createSilentWav(30)

export interface MockLayoutRegion {
  source: "docling"
  label: string
  narrate: boolean
  bbox: [number, number, number, number]
  text?: string
  start?: number
  end?: number
}

export type MockDoclingRegion = MockLayoutRegion

export interface MockExtractedPage {
  page_number: number
  content: string
  bounding_boxes: Array<Record<string, unknown>> | null
  layout_regions?: MockLayoutRegion[] | null
  narration_exclusions?: Array<{ start: number; end: number }> | null
  headings?: unknown
}

export interface HarnessOptions {
  pages?: Record<number, MockExtractedPage>
  pdfPath?: string
  docId?: number
  docTitle?: string
}

export interface SpanRect {
  text: string
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
  normLeft?: number
  normTop?: number
  normRight?: number
  normBottom?: number
}

export interface WhisperPassage {
  id: string
  page_number: number
  phrase: string
  expected_line_text: string
  prev_line_text: string
  next_line_text: string
  column: "left" | "right"
}

export function visualLineCount(spans: SpanRect[]): number {
  const lines: Array<SpanRect & { right: number }> = []
  const sorted = [...spans].sort((a, b) => a.left - b.left)
  for (const span of sorted) {
    const spanRight = span.right ?? (span.left + span.width)
    const spanBottom = span.bottom ?? (span.top + span.height)
    const line = lines.find((l) => {
      const overlap = Math.min(l.bottom, spanBottom) - Math.max(l.top, span.top)
      const gap = Math.max(l.left - span.left - span.width, span.left - l.right, 0)
      return overlap > Math.min(l.height, span.height) / 2 && gap <= Math.max(l.height, span.height)
    })
    if (line) {
      line.left = Math.min(line.left, span.left)
      line.right = Math.max(line.right, spanRight)
      line.top = Math.min(line.top, span.top)
      line.bottom = Math.max(line.bottom, spanBottom)
      line.height = line.bottom - line.top
    } else {
      lines.push({
        ...span,
        right: spanRight,
        bottom: spanBottom,
      })
    }
  }
  return lines.length
}

export function parseCommittedFixtureWithPython(pdfRelativePath: string): Record<number, MockExtractedPage> {
  const pythonBin = process.env.PYTHON_BIN || "/home/mrx/.local/venv/bin/python"
  const workspaceRoot = path.resolve(__dirname, "../..")
  const pdfAbsPath = path.resolve(workspaceRoot, pdfRelativePath)

  const pyCode = `
import json, sys
from app.services.pdf_parser import PDFParserService

pdf_path = sys.argv[1]
svc = PDFParserService()
parsed = svc.parse_pdf(pdf_path)
print(json.dumps(parsed))
`
  const out = execFileSync(pythonBin, ["-c", pyCode, pdfAbsPath], {
    cwd: workspaceRoot,
    env: { ...process.env, PYTHONPATH: path.join(workspaceRoot, "backend") },
    encoding: "utf8",
    maxBuffer: 50 * 1024 * 1024,
    timeout: 30_000,
  })

  const result = JSON.parse(out)
  const pagesMap: Record<number, MockExtractedPage> = {}
  for (const p of result.pages) {
    pagesMap[p.page_number] = {
      page_number: p.page_number,
      content: p.content,
      bounding_boxes: p.bounding_boxes,
      layout_regions: p.layout_regions || null,
      narration_exclusions: p.narration_exclusions || null,
      headings: p.headings || null,
    }
  }
  return pagesMap
}

export function loadWhisperExpectations(): { passages: WhisperPassage[] } {
  const filePath = path.resolve(__dirname, "../fixtures/whisper-expectations.json")
  return JSON.parse(fs.readFileSync(filePath, "utf-8"))
}

export function loadWhisperCorruptPayload(): Record<number, MockExtractedPage> {
  const filePath = path.resolve(__dirname, "../fixtures/whisper-corrupt-payload.json")
  const raw = JSON.parse(fs.readFileSync(filePath, "utf-8"))
  const pagesMap: Record<number, MockExtractedPage> = {}
  for (const p of raw.pages) {
    pagesMap[p.page_number] = {
      page_number: p.page_number,
      content: p.content,
      bounding_boxes: p.bounding_boxes,
      layout_regions: p.layout_regions || null,
      narration_exclusions: p.narration_exclusions || null,
      headings: p.headings || null,
    }
  }
  return pagesMap
}

export async function setupReaderMockHarness(page: Page, options: HarnessOptions = {}) {
  const currentUserId = 101
  const docId = options.docId || 99
  const currentPages = options.pages || {}
  const pageNumbers = Object.keys(currentPages).map(Number)
  const numPages = options.pages ? (pageNumbers.length ? Math.max(...pageNumbers) : 1) : 11
  const currentStatus = "completed"
  let layoutStatus = Object.values(currentPages).every(
    (item) => item.layout_regions != null && item.narration_exclusions != null
  )
    ? "ready"
    : "not_requested"

  const documents = [
    {
      id: docId,
      user_id: currentUserId,
      notebook_id: null,
      title: options.docTitle || "whisper-test.pdf",
      format: "pdf",
      file_size: 276000,
      num_pages: numPages,
      processing_status: currentStatus,
      created_at: "2026-10-29T00:00:00Z",
      last_read_page: 1,
      last_read_at: null,
      last_read_position: null,
    },
  ]

  const releaseEvents: Array<{ sessionId?: string; cacheIds?: number[]; allSession?: boolean }> = []
  const streamRequests: Array<{ text: string; sessionId?: string }> = []
  const pageRequests: Array<{ docId: number; pageNum: number }> = []

  page.on("pageerror", (err) => {
    console.error("[pageerror]", err.message)
  })

  await page.addInitScript((storageUserId) => {
    localStorage.setItem("token", `test-token-${storageUserId}`)
    ;(window as unknown as { __readingAudio: HTMLAudioElement[] }).__readingAudio = []
    const nativeAudio = window.Audio
    ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = undefined
    window.Audio = function (audioSource?: string) {
      const audioElement = new nativeAudio(audioSource)
      ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = audioElement
      ;(window as unknown as { __readingAudio: HTMLAudioElement[] }).__readingAudio.push(audioElement)
      return audioElement
    } as unknown as typeof Audio
  }, currentUserId)

  const pdfFilePath = options.pdfPath || "fixtures/whisper-pages-7-8-11.pdf"

  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()

    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: {
          id: currentUserId,
          username: "whisper.tester",
          email: "whisper.tester@example.com",
          role: "user",
          is_admin: false,
          created_at: "2026-10-01T00:00:00Z",
        },
      })
    }

    if (url.pathname === "/api/settings") {
      return route.fulfill({
        json: {
          llm: { active: "omniroute", backends: [] },
          tts: { default_voice: "test" },
          export: { enabled: false },
        },
      })
    }

    if (url.pathname === "/api/documents" && method === "GET") {
      documents[0].processing_status = currentStatus
      return route.fulfill({ json: documents })
    }

    if (url.pathname === "/api/documents/library") {
      return route.fulfill({
        json: {
          documents,
          total: documents.length,
          library_total: documents.length,
          reading_count: 0,
          processing_count: 0,
          failed_count: 0,
          formats: { pdf: documents.length },
          recent: [],
        },
      })
    }

    if (url.pathname === `/api/documents/${docId}/layout` && method === "GET") {
      return route.fulfill({ json: { status: layoutStatus } })
    }
    if (url.pathname === `/api/documents/${docId}/analyze-layout` && method === "POST") {
      layoutStatus = "ready"
      return route.fulfill({ json: { status: layoutStatus } })
    }

    const docMatch = url.pathname.match(/^\/api\/documents\/(\d+)$/)
    if (docMatch && method === "GET") {
      const id = Number(docMatch[1])
      if (id === docId) {
        documents[0].processing_status = currentStatus
        return route.fulfill({ json: documents[0] })
      }
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    const fileMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/file$/)
    if (fileMatch) {
      return route.fulfill({
        path: path.resolve(__dirname, "..", pdfFilePath),
        contentType: "application/pdf",
      })
    }

    const bookmarksMatch = url.pathname.match(/^\/api\/bookmarks\/(\d+)$/)
    if (bookmarksMatch) {
      return route.fulfill({ json: [] })
    }

    const progressMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/progress$/)
    if (progressMatch && method === "PUT") {
      const id = Number(progressMatch[1])
      const data = request.postDataJSON()
      const found = documents.find((entry) => entry.id === id)
      if (found) {
        found.last_read_page = data.page
        found.last_read_at = new Date().toISOString()
        return route.fulfill({ json: found })
      }
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    if (url.pathname === "/api/tts/stream" && method === "POST") {
      const postData = request.postDataJSON()
      streamRequests.push({ text: postData.text, sessionId: postData.session_id })
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: SILENT_WAV_BUFFER,
        headers: {
          "x-tts-duration": "30.0",
          "x-tts-cache-id": String(streamRequests.length + 100),
          "x-tts-cached": "miss",
        },
      })
    }

    if (url.pathname === "/api/tts/release" && method === "POST") {
      const postData = request.postDataJSON()
      releaseEvents.push({
        sessionId: postData.session_id,
        cacheIds: postData.cache_ids,
        allSession: postData.all_session,
      })
      return route.fulfill({
        status: 200,
        json: {
          status: "ok",
          released_count: postData.cache_ids ? postData.cache_ids.length : 1,
          deleted_count: postData.cache_ids ? postData.cache_ids.length : 1,
        },
      })
    }

    if (url.pathname === "/api/tts/heartbeat" && method === "POST") {
      return route.fulfill({
        status: 200,
        json: { status: "ok", renewed: true },
      })
    }

    const pageTextMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/page\/(\d+)$/)
    if (pageTextMatch && method === "GET") {
      const reqDocId = Number(pageTextMatch[1])
      const pageNum = Number(pageTextMatch[2])
      pageRequests.push({ docId: reqDocId, pageNum })
      const pageData = currentPages[pageNum]
      if (pageData) {
        return route.fulfill({
          json: {
            page_number: pageNum,
            content: pageData.content,
            bounding_boxes: pageData.bounding_boxes,
            layout_regions: pageData.layout_regions || null,
            narration_exclusions: pageData.narration_exclusions || null,
            headings: pageData.headings || null,
          },
        })
      }
      return route.fulfill({ status: 404, json: { detail: "Page not found" } })
    }

    return route.fulfill({ json: [] })
  })

  return {
    releaseEvents,
    streamRequests,
    pageRequests,
  }
}

