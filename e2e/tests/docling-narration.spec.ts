import fs from "fs"
import path from "path"
import { expect, test, type Page } from "@playwright/test"

function createSilentWav(seconds = 30): Buffer {
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

const SILENT_WAV_BUFFER = createSilentWav(30)

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

interface HarnessOptions {
  pages?: Record<number, MockExtractedPage>
  pdfPath?: string
  initialProcessingStatus?: string
  allowReparseTransition?: boolean
}

function loadCommittedParserFixture(): Record<number, MockExtractedPage> {
  const fixturePath = path.resolve(__dirname, "../fixtures/docling-narration-meta.json")
  const rawText = fs.readFileSync(fixturePath, "utf-8")
  const parsedData = JSON.parse(rawText) as {
    pages: Array<{
      page_number: number
      content: string
      bounding_boxes?: Array<Record<string, unknown>> | null
      layout_regions?: MockLayoutRegion[] | null
      narration_exclusions?: Array<{ start: number; end: number }> | null
      headings?: unknown
    }>
  }
  const pagesMap: Record<number, MockExtractedPage> = {}
  for (const pageEntry of parsedData.pages) {
    pagesMap[pageEntry.page_number] = {
      page_number: pageEntry.page_number,
      content: pageEntry.content,
      bounding_boxes: pageEntry.bounding_boxes ?? null,
      layout_regions: pageEntry.layout_regions ?? null,
      narration_exclusions: pageEntry.narration_exclusions ?? null,
      headings: pageEntry.headings,
    }
  }
  return pagesMap
}

function loadRealParserArtifact(): Record<number, MockExtractedPage> {
  const envPath = process.env.DOCLING_PARSED_JSON
  if (!envPath) {
    throw new Error("DOCLING_PARSED_JSON environment variable is not defined")
  }

  const targetPath = path.resolve(envPath)
  if (!fs.existsSync(targetPath)) {
    throw new Error(`Explicit DOCLING_PARSED_JSON provided but file does not exist: ${targetPath}`)
  }

  const rawText = fs.readFileSync(targetPath, "utf-8")
  const parsedData = JSON.parse(rawText) as {
    pages: Array<{
      page_number: number
      content: string
      bounding_boxes?: Array<Record<string, unknown>> | null
      layout_regions?: MockLayoutRegion[] | null
      narration_exclusions?: Array<{ start: number; end: number }> | null
      headings?: unknown
    }>
  }
  const pagesMap: Record<number, MockExtractedPage> = {}
  for (const pageEntry of parsedData.pages) {
    pagesMap[pageEntry.page_number] = {
      page_number: pageEntry.page_number,
      content: pageEntry.content,
      bounding_boxes: pageEntry.bounding_boxes ?? null,
      layout_regions: pageEntry.layout_regions ?? null,
      narration_exclusions: pageEntry.narration_exclusions ?? null,
      headings: pageEntry.headings,
    }
  }
  return pagesMap
}

async function setupDoclingHarness(page: Page, options: HarnessOptions = {}) {
  const currentUserId = 101
  const docId = 99
  let currentPages = options.pages || loadCommittedParserFixture()
  const numPages = Object.keys(currentPages).length
  let currentStatus = options.initialProcessingStatus || "completed"
  let reparseStep = 0

  const documents = [
    {
      id: docId,
      user_id: currentUserId,
      notebook_id: null,
      title: "docling-narration.pdf",
      format: "pdf",
      file_size: 4800,
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
    const nativeAudio = window.Audio
    ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = undefined
    window.Audio = function (audioSource?: string) {
      const audioElement = new nativeAudio(audioSource)
      ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = audioElement
      return audioElement
    } as unknown as typeof Audio
  }, currentUserId)

  const pdfFilePath = options.pdfPath || "fixtures/docling-narration.pdf"

  function advanceReparseState() {
    if (options.allowReparseTransition && currentStatus !== "completed" && currentStatus !== "extracted") {
      if (reparseStep === 1) {
        currentStatus = "extracting"
        reparseStep = 2
      } else if (reparseStep === 2) {
        currentStatus = "extracted"
        currentPages = loadCommittedParserFixture()
      }
    }
  }

  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()

    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: {
          id: currentUserId,
          username: "docling.tester",
          email: "docling.tester@example.com",
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
      advanceReparseState()
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

    const reparseMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/re-parse$/)
    if (reparseMatch && method === "POST") {
      currentStatus = "parsing"
      reparseStep = 1
      documents[0].processing_status = currentStatus
      return route.fulfill({
        json: { status: "ok", message: "Reparse started" },
      })
    }

    const docMatch = url.pathname.match(/^\/api\/documents\/(\d+)$/)
    if (docMatch && method === "GET") {
      const id = Number(docMatch[1])
      if (id === docId) {
        advanceReparseState()
        documents[0].processing_status = currentStatus
        return route.fulfill({ json: documents[0] })
      }
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    const fileMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/file$/)
    if (fileMatch) {
      return route.fulfill({
        path: pdfFilePath,
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

test.describe("Docling Layout Narration and Visual Overlay", () => {
  test("narrates only body and title while completely skipping captions, tables, and page headers", async ({ page }) => {
    const harness = await setupDoclingHarness(page)

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const playButton = page.getByRole("button", { name: "Play (Space)", exact: true })
    await expect(playButton).toBeVisible()
    await playButton.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    await expect.poll(async () => {
      const spoken = harness.streamRequests.map((req) => req.text).join(" ")
      if (spoken.includes("The experimental results demonstrate that neural layout analysis prevents unnatural TTS reading")) {
        return true
      }
      await page.evaluate(() => {
        const win = window as any
        const audio = win.ttsEngine?.audio || win.__capturedAudioElement
        const lastSrc = win.__lastHandledAudioSrc || ""
        if (audio && !audio.paused && audio.src && audio.src !== lastSrc && audio.readyState >= 1) {
          win.__lastHandledAudioSrc = audio.src
          audio.dispatchEvent(new Event("ended"))
        }
      })
      return false
    }, { timeout: 15000, intervals: [200, 300, 500] }).toBe(true)

    const spokenChunks = harness.streamRequests.map((req) => req.text)
    const combinedSpoken = spokenChunks.join(" ")

    expect(combinedSpoken).toContain("Neural Layout Analysis for Voice Narration")
    expect(combinedSpoken).toContain("Speech narration in digital reading systems requires extracting main body content")
    expect(combinedSpoken).toContain("Benchmark Evaluation")
    expect(combinedSpoken).toContain("We evaluate deep parsing models across structured academic articles and reports.")
    expect(combinedSpoken).toContain("The experimental results demonstrate that neural layout analysis prevents unnatural TTS reading")

    expect(combinedSpoken).not.toContain("Journal of Automated Document Intelligence")
    expect(combinedSpoken).not.toContain("Architecture Precision Recall")
    expect(combinedSpoken).not.toContain("Baseline Parser")
    expect(combinedSpoken).not.toContain("Docling Hybrid")
    expect(combinedSpoken).not.toContain("Table 1:")
    expect(combinedSpoken).not.toContain("Quantitative evaluation comparing baseline parser")
    expect(combinedSpoken).not.toContain("Page 1 of 3")
  })

  test("clicking rejected text does not start narration, while clicking readable body text starts speech", async ({ page }) => {
    const harness = await setupDoclingHarness(page)

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const textLayer = page.locator("#pdf-page-container-1 .textLayer")
    await expect(textLayer).toBeVisible()

    const captionSpan = textLayer.locator("span", { hasText: "Table 1:" }).first()
    await expect(captionSpan).toBeVisible()
    await captionSpan.click()

    await page.waitForTimeout(500)
    expect(harness.streamRequests.length).toBe(0)

    const headerSpan = textLayer.locator("span", { hasText: "Journal of Automated Document Intelligence" }).first()
    if (await headerSpan.isVisible()) {
      await headerSpan.click()
      await page.waitForTimeout(500)
      expect(harness.streamRequests.length).toBe(0)
    }

    const bodySpan = textLayer.locator("span", { hasText: "Speech narration in digital reading systems" }).first()
    await expect(bodySpan).toBeVisible()
    await bodySpan.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const firstSpoken = harness.streamRequests[0].text
    expect(firstSpoken).toContain("Speech narration in digital reading systems")
    expect(firstSpoken).not.toContain("Journal of Automated Document Intelligence")
    expect(firstSpoken).not.toContain("Table 1:")

    const lastBodySpan = textLayer.locator("span", { hasText: "and captions." }).first()
    await lastBodySpan.click()
    await expect(textLayer.locator(".tts-sentence-active").first()).toBeVisible()
    await expect.poll(() => textLayer.evaluate((layer) => layer.scrollTop)).toBe(0)
    await expect.poll(() => textLayer.evaluate((layer) => layer.scrollLeft)).toBe(0)
  })

  test("reading overlay defaults to off, toggles on/off with correct accessible labels, green/red boxes, and scales on zoom", async ({ page }) => {
    await setupDoclingHarness(page)

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const toggleButton = page.getByTestId("docling-overlay-toggle")
    await expect(toggleButton).toBeVisible()
    await expect(toggleButton).toHaveAttribute("aria-pressed", "false")
    await expect(toggleButton).toHaveAttribute("aria-label", "Toggle reading overlay")

    const page1Container = page.locator("#pdf-page-container-1")
    const page1Overlay = page1Container.getByTestId("docling-overlay-container")
    await expect(page1Overlay).toHaveCount(0)

    await toggleButton.click()
    await expect(toggleButton).toHaveAttribute("aria-pressed", "true")
    await expect(page1Overlay).toBeVisible()

    const boxes = page1Container.getByTestId("docling-box")
    const readableBoxes = page1Container.locator('[data-testid="docling-box"][data-status="readable"]')
    const skippedBoxes = page1Container.locator('[data-testid="docling-box"][data-status="skipped"]')

    await expect(boxes.first()).toBeVisible()
    const readableCount = await readableBoxes.count()
    const skippedCount = await skippedBoxes.count()
    expect(readableCount).toBeGreaterThanOrEqual(1)
    expect(skippedCount).toBeGreaterThanOrEqual(1)

    const legend = page1Container.getByTestId("docling-overlay-legend")
    await expect(legend).toBeVisible()
    await expect(legend).toContainText("readable")
    await expect(legend).toContainText("skipped")

    const firstBox = boxes.first()
    const initialBoxRect = await firstBox.boundingBox()
    expect(initialBoxRect).not.toBeNull()

    const zoomInButton = page.getByRole("button", { name: "Zoom in" })
    if (await zoomInButton.isVisible()) {
      await zoomInButton.click()
      await page.waitForTimeout(500)
      const zoomedBoxRect = await firstBox.boundingBox()
      expect(zoomedBoxRect).not.toBeNull()
      if (initialBoxRect && zoomedBoxRect) {
        expect(zoomedBoxRect.width).toBeGreaterThan(initialBoxRect.width)
      }
    }

    const scratchDir = path.resolve(__dirname, "../../_scratch/docling-verification")
    fs.mkdirSync(scratchDir, { recursive: true })
    const screenshotPath = path.join(scratchDir, "docling-overlay-desktop.png")
    await page.screenshot({ path: screenshotPath })
    expect(fs.existsSync(screenshotPath)).toBe(true)

    const pageScreenshotPath = path.join(scratchDir, "docling-overlay-page.png")
    await page1Container.screenshot({ path: pageScreenshotPath })
    expect(fs.existsSync(pageScreenshotPath)).toBe(true)

    await toggleButton.click()
    await expect(toggleButton).toHaveAttribute("aria-pressed", "false")
    await expect(page1Overlay).toHaveCount(0)
  })

  test("excluded-only page skips without rejected audio requests and continues narration on next readable page", async ({ page }) => {
    const harness = await setupDoclingHarness(page)

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const playButton = page.getByRole("button", { name: "Play (Space)", exact: true })
    await expect(playButton).toBeVisible()
    await playButton.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    await expect.poll(async () => {
      const allSpoken = harness.streamRequests.map((req) => req.text).join(" ")
      if (allSpoken.includes("Conclusion and Future Work")) return true

      await page.evaluate(() => {
        const win = window as any
        const audio = win.ttsEngine?.audio || win.__capturedAudioElement
        const lastSrc = win.__lastHandledAudioSrc || ""
        if (audio && !audio.paused && audio.src && audio.src !== lastSrc && audio.readyState >= 1) {
          win.__lastHandledAudioSrc = audio.src
          audio.dispatchEvent(new Event("ended"))
        }
      })
      return false
    }, { timeout: 20000, intervals: [200, 300, 500] }).toBe(true)

    const allSpokenTexts = harness.streamRequests.map((req) => req.text).join(" ")
    expect(allSpokenTexts).not.toContain("Parameter")
    expect(allSpokenTexts).not.toContain("Batch Size")
    expect(allSpokenTexts).not.toContain("Table 2:")

    const requestedPages = harness.pageRequests.map((req) => req.pageNum)
    expect(requestedPages).toContain(1)
    expect(requestedPages).toContain(2)
    expect(requestedPages).toContain(3)
  })

  test("legacy unclassified page preserves fallback filters and honestly displays unavailable overlay notice", async ({ page }) => {
    const legacyPages: Record<number, MockExtractedPage> = {
      1: {
        page_number: 1,
        content: "Legacy document content without Docling classification.\nThis page has older bounding box format.\nTable of contents . . . 12",
        bounding_boxes: [
          { text: "Legacy", bbox: [72, 60, 98, 75] },
          { text: "document", bbox: [102, 60, 150, 75] },
        ],
      },
    }

    const harness = await setupDoclingHarness(page, { pages: legacyPages, pdfPath: "fixtures/e2e-sample.pdf" })

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const toggleButton = page.getByTestId("docling-overlay-toggle")
    await expect(toggleButton).toBeVisible()
    await toggleButton.click()

    const unavailableNotice = page.getByTestId("docling-overlay-unavailable")
    await expect(unavailableNotice).toBeVisible()
    await expect(unavailableNotice).toContainText("Reading overlay unavailable")

    const playButton = page.getByRole("button", { name: "Play (Space)", exact: true })
    await playButton.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const legacySpoken = harness.streamRequests.map((req) => req.text).join(" ")
    expect(legacySpoken).toContain("Legacy document content without Docling classification.")
  })

  test("phone viewport renders toggle cleanly without document horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await setupDoclingHarness(page)

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const toggleButton = page.getByTestId("docling-overlay-toggle")
    await expect(toggleButton).toBeVisible()

    const hasHorizontalOverflow = await page.evaluate(() => {
      const docWidth = document.documentElement.scrollWidth
      const winWidth = window.innerWidth
      return docWidth > winWidth
    })
    expect(hasHorizontalOverflow).toBe(false)
  })

  test("live Docling parser verification artifact executes end-to-end when available", async ({ page }) => {
    test.skip(!process.env.DOCLING_PARSED_JSON, "DOCLING_PARSED_JSON environment variable not provided; skipping live parser test.")

    const realArtifact = loadRealParserArtifact()
    const harness = await setupDoclingHarness(page, { pages: realArtifact })

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const toggleButton = page.getByTestId("docling-overlay-toggle")
    await expect(toggleButton).toBeVisible()
    await toggleButton.click()

    const boxes = page.getByTestId("docling-box")
    await expect(boxes.first()).toBeVisible()

    const playButton = page.getByRole("button", { name: "Play (Space)", exact: true })
    await playButton.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const spoken = harness.streamRequests.map((req) => req.text).join(" ")
    expect(spoken).not.toContain("Journal of Automated Document Intelligence")
    expect(spoken).not.toContain("Table 1:")
  })

  test("re-parse transitions document from legacy to Docling-classified and enables overlay and clean narration without page reload", async ({ page }) => {
    const legacyPages: Record<number, MockExtractedPage> = {
      1: {
        page_number: 1,
        content: "Neural Layout Analysis for Voice Narration\nTable 1: Quantitative evaluation.\nBody paragraph for reading.",
        bounding_boxes: [
          { text: "Neural", bbox: [72, 60, 98, 75] },
        ],
      },
    }

    const harness = await setupDoclingHarness(page, {
      pages: legacyPages,
      initialProcessingStatus: "completed",
      allowReparseTransition: true,
    })

    await page.goto("/dashboard/read/99")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const toggleButton = page.getByTestId("docling-overlay-toggle")
    await expect(toggleButton).toBeVisible()
    await toggleButton.click()

    const unavailableNotice = page.getByTestId("docling-overlay-unavailable")
    await expect(unavailableNotice).toBeVisible()

    const reparseButton = page.getByRole("button", { name: "Re-parse", exact: true })
    await expect(reparseButton).toBeVisible()
    await reparseButton.click()

    await expect(reparseButton).toBeEnabled({ timeout: 15000 })
    await expect(page.getByRole("button", { name: "Re-parsing…", exact: true })).toHaveCount(0)

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 10000 })

    const remountedToggle = page.getByTestId("docling-overlay-toggle")
    await expect(remountedToggle).toBeVisible()
    const isPressed = await remountedToggle.getAttribute("aria-pressed")
    if (isPressed === "false") {
      await remountedToggle.click()
    }
    await expect(page.getByTestId("docling-box").first()).toBeVisible({ timeout: 10000 })

    const playButton = page.getByRole("button", { name: "Play (Space)", exact: true })
    await playButton.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const spoken = harness.streamRequests.map((req) => req.text).join(" ")
    expect(spoken).not.toContain("Table 1:")
    expect(spoken).toContain("Speech narration in digital reading systems")
  })
})
