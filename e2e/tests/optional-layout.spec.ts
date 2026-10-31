import { expect, test, type Page } from "@playwright/test"
import { spawn, type ChildProcess } from "node:child_process"
import fs from "node:fs"
import http from "node:http"
import net from "node:net"
import path from "node:path"
import { libraryDocuments, mockLibrary } from "./library-fixture"

// Real production frontend and PDF.js, with deterministic API responses.
// This suite does not exercise Celery or the Docling model.
let server: ChildProcess
let serverUrl = ""
const fixture = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../fixtures/docling-narration-meta.json"), "utf8"))
const regions = [
  { source: "docling", label: "text", narrate: true, bbox: [0.05, 0.1, 0.95, 0.17] },
  { source: "docling", label: "caption", narrate: false, bbox: [0.05, 0.32, 0.95, 0.35] },
]
type LayoutStatus = "not_requested" | "processing" | "ready" | "failed"
const hyphenatedLines = ["Line highlighting fixture", "Experiments use trans-", "formations across two physical lines."]

function singlePagePdf(lines: string[]) {
  const stream = `BT\n/F1 14 Tf\n72 720 Td\n${lines.map((line, index) => `${index ? "0 -18 Td\n" : ""}(${line}) Tj`).join("\n")}\nET`
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ]
  let pdf = "%PDF-1.4\n"
  const offsets = [0]
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${index + 1} 0 obj\n${object}\nendobj\n` })
  const xref = Buffer.byteLength(pdf)
  pdf += `xref\n0 ${offsets.length}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(pdf)
}

function silentWav() {
  const dataSize = 8000 * 30 * 2
  const buffer = Buffer.alloc(44 + dataSize)
  buffer.write("RIFF", 0)
  buffer.writeUInt32LE(36 + dataSize, 4)
  buffer.write("WAVEfmt ", 8)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(8000, 24)
  buffer.writeUInt32LE(16000, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write("data", 36)
  buffer.writeUInt32LE(dataSize, 40)
  return buffer
}

async function harness(page: Page, options: { status?: LayoutStatus; empty?: boolean; extracting?: boolean; hyphenated?: boolean } = {}) {
  await mockLibrary(page)
  const state = {
    status: options.status ?? "not_requested" as LayoutStatus,
    extracting: options.extracting ?? false,
    posts: [] as number[],
    gets: [] as number[],
    pageGets: 0,
    streams: [] as string[],
    postDelay: 0,
    rejectPost: false,
    nextPageDelay: 0,
    nextPageRequests: 0,
  }
  const document = (id: number) => ({ ...libraryDocuments[0], id, num_pages: options.hyphenated ? 1 : 3, processing_status: state.extracting ? "parsing" : "summarizing_pages" })
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    if (/^\/api\/documents\/\d+\/re-parse$/.test(url.pathname)) {
      state.status = "not_requested"
      state.extracting = true
      return route.fulfill({ json: { status: "ok" } })
    }
    const layout = url.pathname.match(/^\/api\/documents\/(\d+)\/(layout|analyze-layout)$/)
    if (layout) {
      const id = Number(layout[1])
      if (method === "POST") {
        state.posts.push(id)
        if (state.postDelay) await new Promise((resolve) => setTimeout(resolve, state.postDelay))
        if (state.rejectPost) return route.fulfill({ status: 503, json: { detail: "Unavailable" } })
        state.status = "processing"
      } else {
        state.gets.push(id)
      }
      return route.fulfill({ json: { status: id === 2 ? "not_requested" : state.status, ...(state.status === "failed" ? { error: "Layout worker failed" } : {}) } })
    }
    if (url.pathname === "/api/documents/upload") return route.fulfill({ json: document(1) })
    if (/^\/api\/documents\/\d+$/.test(url.pathname)) return route.fulfill({ json: document(Number(url.pathname.split("/").pop())) })
    if (/^\/api\/documents\/\d+\/status$/.test(url.pathname)) return route.fulfill({ json: { processing_status: state.extracting ? "parsing" : "extracted" } })
    if (/^\/api\/documents\/\d+\/file$/.test(url.pathname)) return route.fulfill({ ...(options.hyphenated ? { body: singlePagePdf(hyphenatedLines) } : { path: "fixtures/docling-narration.pdf" }), contentType: "application/pdf" })
    const pageMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/page\/(\d+)$/)
    if (pageMatch) {
      state.pageGets++
      if (state.extracting) return route.fulfill({ status: 404, json: { detail: "Page not found" } })
      const data = options.hyphenated ? { page_number: 1, content: hyphenatedLines.join("\n") } : fixture.pages[Number(pageMatch[2]) - 1]
      const excludedText = data.content.split("\n")[0]
      const payload = {
        ...data,
        bounding_boxes: null,
        layout_regions: state.status === "ready" && !options.empty ? regions : [],
        narration_exclusions: state.status === "ready" ? [{ start: 0, end: excludedText.length }] : [],
      }
      if (Number(pageMatch[2]) === 2) {
        state.nextPageRequests++
        if (state.nextPageDelay) await new Promise((resolve) => setTimeout(resolve, state.nextPageDelay))
      }
      return route.fulfill({ json: payload })
    }
    if (url.pathname === "/api/tts/stream") {
      state.streams.push(route.request().postDataJSON().text)
      return route.fulfill({ contentType: "audio/wav", body: silentWav(), headers: { "x-tts-duration": "30", "x-tts-cache-id": String(state.streams.length) } })
    }
    return route.fallback()
  })
  return state
}

async function openReader(page: Page, id = 1) {
  await page.goto(`${serverUrl}/dashboard/read/${id}`)
  await expect(page.getByTestId("docling-overlay-toggle")).toBeEnabled()
  await expect(page.locator("#pdf-page-container-1 .textLayer span").first()).toBeVisible()
}

test.describe("Optional layout analysis, mocked API", () => {
  test.beforeAll(async () => {
    const port = await new Promise<number>((resolve) => {
      const socket = net.createServer()
      socket.listen(0, "127.0.0.1", () => {
        const address = socket.address() as net.AddressInfo
        socket.close(() => resolve(address.port))
      })
    })
    serverUrl = `http://127.0.0.1:${port}`
    server = spawn("./node_modules/.bin/next", ["start", "-p", String(port), "-H", "127.0.0.1"], { cwd: path.resolve(__dirname, "../../frontend"), stdio: "pipe" })
    await expect.poll(async () => new Promise<boolean>((resolve) => {
      const request = http.get(`${serverUrl}/login`, (response) => { response.resume(); resolve(response.statusCode === 200) })
      request.setTimeout(1000, () => request.destroy())
      request.on("error", () => resolve(false))
    }), { timeout: 20000 }).toBe(true)
  })
  test.afterAll(() => { server?.kill("SIGTERM") })

  test("upload opens the PDF before extraction and retries text after initial 404", async ({ page }) => {
    const state = await harness(page, { extracting: true })
    await page.goto(`${serverUrl}/dashboard`)
    await page.getByRole("button", { name: "Add documents", exact: true }).click()
    await page.locator('input[type="file"]').setInputFiles("fixtures/docling-narration.pdf")
    const open = page.getByRole("link", { name: "Open document", exact: true })
    await expect(open).toBeVisible()
    await expect(open).toHaveAttribute("href", "/dashboard/read/1")
    await expect(open.locator("..").locator("..").getByText("Processing", { exact: true })).toBeVisible()
    await open.click()
    await expect(page.getByRole("heading", { name: "Add sources" })).toHaveCount(0)
    await expect(page.locator("#pdf-page-container-1 canvas")).toBeVisible()
    const line = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: "Speech narration in digital reading systems" }).first()
    await line.click()
    await expect(page.getByText("Text extraction is in progress. Speech will be ready once extraction completes.")).toBeVisible()
    expect(state.streams).toHaveLength(0)
    expect(state.posts).toHaveLength(0)
    const before = state.pageGets
    state.extracting = false
    await expect(page.getByText("extracting text…", { exact: true })).toHaveCount(0, { timeout: 6000 })
    await line.click()
    await expect.poll(() => state.streams.length).toBeGreaterThan(0)
    expect(state.pageGets).toBeGreaterThan(before)
    await expect(line).toHaveClass(/tts-sentence-(active|preparing)/)
    const continuation = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: "data, captions, and running page headers." }).first()
    await expect(continuation).not.toHaveClass(/tts-sentence-(active|preparing)/)
  })

  test("first click requests once, polls ready, refreshes in place, and later toggles reuse results", async ({ page }) => {
    const state = await harness(page)
    await openReader(page)
    const toggle = page.getByTestId("docling-overlay-toggle")
    expect(state.posts).toHaveLength(0)
    await page.waitForTimeout(2300)
    expect(state.gets).toHaveLength(1)
    const canvas = page.locator("#pdf-page-container-1 canvas")
    await canvas.evaluate((element) => { element.dataset.originalCanvas = "true" })
    const scroll = page.locator("#pdf-page-container-1").locator("..")
    await scroll.evaluate((element) => { element.scrollTop = 120 })
    const scrollBefore = await scroll.evaluate((element) => element.scrollTop)
    state.postDelay = 350
    await toggle.evaluate((element) => { for (let i = 0; i < 5; i++) (element as HTMLButtonElement).click() })
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    expect(state.posts).toEqual([1])
    await expect.poll(() => state.status).toBe("processing")
    state.status = "ready"
    await expect(page.locator('#pdf-page-container-1 [data-status="readable"]')).toHaveCount(1)
    await expect(page.locator('#pdf-page-container-1 [data-status="skipped"]')).toHaveCount(1)
    await expect(canvas).toHaveAttribute("data-original-canvas", "true")
    expect(await scroll.evaluate((element) => element.scrollTop)).toBe(scrollBefore)
    const getCount = state.gets.length
    await toggle.click()
    await expect(page.getByTestId("docling-overlay-container")).toHaveCount(0)
    await toggle.click()
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
    await page.waitForTimeout(2300)
    expect(state.posts).toEqual([1])
    expect(state.gets).toHaveLength(getCount)
    await scroll.evaluate((element) => { element.scrollTop = 0 })
    await page.screenshot({ path: path.resolve(__dirname, "../../_scratch/optional-layout-ready.png") })
  })

  test("ready with empty regions stays ready after toggling and reload", async ({ page }) => {
    const state = await harness(page, { status: "ready", empty: true })
    await openReader(page)
    const toggle = page.getByTestId("docling-overlay-toggle")
    await toggle.click()
    await expect(page.getByTestId("docling-overlay-unavailable").first()).toContainText("No reading regions found")
    await toggle.click()
    await toggle.click()
    await page.reload()
    await expect(toggle).toBeEnabled()
    await toggle.click()
    await expect(page.getByTestId("docling-overlay-unavailable").first()).toBeVisible()
    expect(state.posts).toHaveLength(0)
  })

  test("processing survives reload, and a failed run offers a nonblocking retry", async ({ page }) => {
    const state = await harness(page, { status: "processing" })
    await openReader(page)
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    await page.reload()
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    await expect(page.locator("#pdf-page-container-1 .textLayer span").first()).toBeVisible()
    await page.screenshot({ path: path.resolve(__dirname, "../../_scratch/optional-layout-processing.png") })
    expect(state.posts).toHaveLength(0)
    state.status = "failed"
    await expect(page.getByText("Layout worker failed", { exact: true })).toBeVisible()
    await page.screenshot({ path: path.resolve(__dirname, "../../_scratch/optional-layout-failed.png") })
    await page.getByRole("button", { name: "Retry layout analysis" }).click()
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    expect(state.posts).toEqual([1])
    await page.getByTitle("Zoom in").click()
    state.status = "ready"
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
  })

  test("request failure can retry and changing documents does not reuse layout state", async ({ page }) => {
    const state = await harness(page)
    state.rejectPost = true
    await openReader(page)
    await page.getByTestId("docling-overlay-toggle").click()
    await expect(page.getByText("Could not start layout analysis. Please retry.")).toBeVisible()
    state.rejectPost = false
    await page.getByRole("button", { name: "Retry layout analysis" }).click()
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    await openReader(page, 2)
    await expect(page.getByTestId("layout-analysis-status")).toHaveCount(0)
    await expect(page.getByTestId("docling-overlay-toggle")).toHaveAttribute("aria-pressed", "false")
    await page.getByTestId("docling-overlay-toggle").click()
    await expect.poll(() => state.posts).toEqual([1, 1, 2])
  })

  test("layout completion preserves active line highlighting and refreshes the next reading", async ({ page }) => {
    const state = await harness(page)
    await openReader(page)
    const line = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: "Speech narration in digital reading systems" }).first()
    const continuation = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: "data, captions, and running page headers." }).first()
    await line.click()
    await expect(line).toHaveClass(/tts-sentence-active/)
    await expect(continuation).not.toHaveClass(/tts-sentence-active/)
    await page.getByTestId("docling-overlay-toggle").click()
    await expect(page.getByTestId("layout-analysis-status")).toBeVisible()
    state.status = "ready"
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
    await expect(line).toHaveClass(/tts-sentence-active/)
    await expect(continuation).not.toHaveClass(/tts-sentence-active/)
    const streamsBefore = state.streams.length
    await line.click()
    await expect.poll(() => state.streams.length).toBeGreaterThan(streamsBefore)
    await expect(line).toHaveClass(/tts-sentence-active/)
    await expect(continuation).not.toHaveClass(/tts-sentence-active/)
  })

  test("reparse discards the old ready overlay and permits a new layout request without remount", async ({ page }) => {
    const state = await harness(page, { status: "ready" })
    await openReader(page)
    const toggle = page.getByTestId("docling-overlay-toggle")
    await toggle.click()
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
    const canvas = page.locator("#pdf-page-container-1 canvas")
    await canvas.evaluate((element) => { element.dataset.originalCanvas = "true" })
    await page.getByRole("button", { name: "Re-parse", exact: true }).click()
    await expect(page.getByTestId("docling-overlay-container")).toHaveCount(0)
    await expect(toggle).toBeEnabled()
    state.extracting = false
    await expect(page.getByText("extracting text…", { exact: true })).toHaveCount(0, { timeout: 6000 })
    await expect(canvas).toHaveAttribute("data-original-canvas", "true")
    await toggle.click()
    await expect.poll(() => state.posts).toEqual([1])
    await expect.poll(() => state.status).toBe("processing")
    state.status = "ready"
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
  })

  test("hyphenated speech keeps each physical line highlighted before and after layout completion", async ({ page }) => {
    const state = await harness(page, { hyphenated: true })
    await openReader(page)
    const first = page.locator(".textLayer span").filter({ hasText: hyphenatedLines[1] }).first()
    const second = page.locator(".textLayer span").filter({ hasText: hyphenatedLines[2] }).first()
    await first.click()
    await expect(first).toHaveClass(/tts-sentence-active/)
    await expect(second).not.toHaveClass(/tts-sentence-active/)
    await page.getByTestId("docling-overlay-toggle").click()
    await expect.poll(() => state.status).toBe("processing")
    state.status = "ready"
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
    await expect(first).toHaveClass(/tts-sentence-active/)
    await expect(second).not.toHaveClass(/tts-sentence-active/)
    await second.click()
    await expect(second).toHaveClass(/tts-sentence-active/)
    await expect(first).not.toHaveClass(/tts-sentence-active/)
  })

  test("layout completion replaces prefetched future-page audio while preserving current playback", async ({ page }) => {
    const state = await harness(page)
    await openReader(page)
    const line = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: /^and captions\.$/ }).first()
    await line.click()
    await expect.poll(() => state.streams.some((text) => text.includes("Parameter Configuration Value Hardware Target"))).toBe(true)
    await expect(line).toHaveClass(/tts-sentence-active/)
    const streamsBefore = state.streams.length
    await page.getByTestId("docling-overlay-toggle").click()
    await expect.poll(() => state.status).toBe("processing")
    state.status = "ready"
    await expect(page.getByTestId("docling-box").first()).toBeVisible()
    await expect.poll(() => state.streams.slice(streamsBefore).some((text) => text.includes("Batch Size 32 samples"))).toBe(true)
    expect(state.streams.slice(streamsBefore).every((text) => !text.includes("Parameter Configuration Value Hardware Target"))).toBe(true)
    await expect(line).toHaveClass(/tts-sentence-active/)
  })

  test("a pre-layout page request completing late cannot restore stale future audio", async ({ page }) => {
    const state = await harness(page)
    state.nextPageDelay = 4500
    await openReader(page)
    const line = page.locator("#pdf-page-container-1 .textLayer span").filter({ hasText: /^and captions\.$/ }).first()
    await line.click()
    await expect.poll(() => state.nextPageRequests).toBe(1)
    state.nextPageDelay = 0
    await page.getByTestId("docling-overlay-toggle").click()
    await expect.poll(() => state.status).toBe("processing")
    state.status = "ready"
    await expect.poll(() => state.streams.some((text) => text.includes("Batch Size 32 samples"))).toBe(true)
    await page.waitForTimeout(3000)
    expect(state.streams.every((text) => !text.includes("Parameter Configuration Value Hardware Target"))).toBe(true)
    await expect(line).toHaveClass(/tts-sentence-active/)
  })
})
