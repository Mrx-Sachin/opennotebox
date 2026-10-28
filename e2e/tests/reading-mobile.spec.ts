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

interface MockDoc {
  id: number
  user_id: number
  notebook_id: number | null
  title: string
  format: string
  file_size: number
  num_pages: number
  processing_status: string
  created_at: string
  last_read_page: number | null
  last_read_at: string | null
  last_read_position: number | null
}

function createDocs(ownerUserId: number): MockDoc[] {
  return [
    {
      id: 60,
      user_id: ownerUserId,
      notebook_id: null,
      title: "QA Notebook Guide.pdf",
      format: "pdf",
      file_size: 24000,
      num_pages: 5,
      processing_status: "completed",
      created_at: "2026-10-01T00:00:00Z",
      last_read_page: 1,
      last_read_at: null,
      last_read_position: null,
    },
    {
      id: 61,
      user_id: ownerUserId,
      notebook_id: null,
      title: "QA Research Notes.pdf",
      format: "pdf",
      file_size: 24000,
      num_pages: 2,
      processing_status: "completed",
      created_at: "2026-10-02T00:00:00Z",
      last_read_page: 1,
      last_read_at: null,
      last_read_position: null,
    },
  ]
}

async function setupMobileHarness(page: Page) {
  const currentUserId = 101
  const documents = createDocs(currentUserId)
  const releaseEvents: Array<{ sessionId?: string; cacheIds?: number[]; allSession?: boolean }> = []
  const streamRequests: Array<{ text: string; sessionId?: string }> = []

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

  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()

    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: {
          id: currentUserId,
          username: `user_${currentUserId}`,
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

    if (url.pathname === "/api/documents") {
      const userDocs = documents.filter((entry) => entry.user_id === currentUserId)
      return route.fulfill({ json: userDocs })
    }

    if (url.pathname === "/api/documents/library") {
      const userDocs = documents.filter((entry) => entry.user_id === currentUserId)
      return route.fulfill({
        json: {
          documents: userDocs,
          total: userDocs.length,
          library_total: userDocs.length,
          reading_count: 0,
          processing_count: 0,
          failed_count: 0,
          formats: { pdf: userDocs.length },
          recent: [],
        },
      })
    }

    const docMatch = url.pathname.match(/^\/api\/documents\/(\d+)$/)
    if (docMatch && method === "GET") {
      const docId = Number(docMatch[1])
      const found = documents.find((entry) => entry.id === docId && entry.user_id === currentUserId)
      if (found) return route.fulfill({ json: found })
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    const fileMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/file$/)
    if (fileMatch) {
      return route.fulfill({
        path: "./fixtures/e2e-sample.pdf",
        contentType: "application/pdf",
      })
    }

    const bookmarksMatch = url.pathname.match(/^\/api\/bookmarks\/(\d+)$/)
    if (bookmarksMatch) {
      return route.fulfill({ json: [] })
    }

    const progressMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/progress$/)
    if (progressMatch && method === "PUT") {
      const docId = Number(progressMatch[1])
      const data = request.postDataJSON()
      const found = documents.find((entry) => entry.id === docId && entry.user_id === currentUserId)
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
      const docId = Number(pageTextMatch[1])
      const pageNum = Number(pageTextMatch[2])
      const sentences = [
        `First narrative sentence of document ${docId} page ${pageNum}.`,
        `Second sequential sentence elaborating details clearly for mobile reading.`,
        `Third concluding thought ensuring smooth reading experience.`,
      ]
      return route.fulfill({
        json: {
          page_number: pageNum,
          content: sentences.join(" "),
        },
      })
    }

    return route.fulfill({ json: [] })
  })

  return {
    releaseEvents,
    streamRequests,
  }
}

test.describe("Mobile reading experience", () => {
  test.use({
    viewport: { width: 375, height: 667 },
    hasTouch: true,
    isMobile: true,
  })

  test("validates 375px mobile viewport, touch targets, overflow, modal expansion, and open in book", async ({ page }) => {
    const harness = await setupMobileHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; pointer-events: none !important; }" })

    const playBtn = page.getByRole("button", { name: "Play (Space)", exact: true })
    await expect(playBtn).toBeVisible()
    await playBtn.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    const backLink = page.getByRole("link", { name: "Back to library" })
    if (await backLink.isVisible()) {
      await backLink.click()
    } else {
      await page.locator('header a[href^="/dashboard"]').first().click()
    }
    await page.waitForURL(/\/dashboard(?:\?.*)?$/)
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; pointer-events: none !important; }" })
    const bottomBar = page.getByTestId("book-bottom-bar")
    await expect(bottomBar).toBeVisible()

    await page.screenshot({ path: "/tmp/phone-reading-bottom-player.png" })

    const pageOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth
    })
    expect(pageOverflow).toBe(true)

    const bottomPlayBtn = page.getByTestId("book-bottom-play-btn")
    await expect(bottomPlayBtn).toBeVisible()

    await page.getByTestId("book-bottom-next-page").click()
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 2 of 5")

    await page.getByTestId("book-bottom-title").click()
    const modal = page.getByTestId("now-reading-modal")
    await expect(modal).toBeVisible()

    await page.screenshot({ path: "/tmp/phone-reading-now-modal.png" })

    const modalPlayBtn = page.getByTestId("now-reading-play-btn")
    await expect(modalPlayBtn).toBeVisible()
    const modalPlayBox = await modalPlayBtn.boundingBox()
    expect(modalPlayBox).not.toBeNull()
    if (modalPlayBox) {
      expect(modalPlayBox.width).toBeGreaterThanOrEqual(44)
      expect(modalPlayBox.height).toBeGreaterThanOrEqual(44)
    }

    const modalOverflow = await modal.evaluate((el) => {
      return el.scrollWidth <= el.clientWidth
    })
    expect(modalOverflow).toBe(true)

    const sentence0 = page.getByTestId("sentence-item-0")
    const sentence1 = page.getByTestId("sentence-item-1")
    await expect(sentence0).toBeVisible()
    await expect(sentence1).toBeVisible()

    await expect(sentence0).toContainText("First narrative sentence")
    await expect(sentence1).toContainText("Second sequential sentence")

    await sentence1.click()
    await expect(sentence1).toHaveAttribute("data-active", "true")

    const openInBookLink = page.getByTestId("now-reading-open-book")
    await expect(openInBookLink).toBeVisible()
    await expect(openInBookLink).toContainText("Page 2")
    await openInBookLink.click()

    await expect(page).toHaveURL(/\/dashboard\/read\/60/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; pointer-events: none !important; }" })

    const pageInput = page.locator('input[type="number"]').first()
    await expect(pageInput).toHaveValue("2")

    await page.screenshot({ path: "/tmp/phone-reading-opened-in-book.png" })

    const returnAudioState = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(returnAudioState).toBe(true)
  })
})

  test("clicking another line seeks in-place without triggering empty state refresh", async ({ page }) => {
    const harness = await setupMobileHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; pointer-events: none !important; }" })

    const playBtn = page.getByRole("button", { name: "Play (Space)", exact: true })
    await expect(playBtn).toBeVisible()
    await playBtn.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    const backLink = page.getByRole("link", { name: "Back to library" })
    if (await backLink.isVisible()) {
      await backLink.click()
    } else {
      await page.locator('header a[href^="/dashboard"]').first().click()
    }
    await page.waitForURL(/\/dashboard(?:\?.*)?$/)
    const bottomBar = page.getByTestId("book-bottom-bar")
    await expect(bottomBar).toBeVisible()
    await page.getByTestId("book-bottom-title").click()
    const modal = page.getByTestId("now-reading-modal")
    await expect(modal).toBeVisible()

    const firstSentence = page.getByTestId("sentence-item-0")
    const secondSentence = page.getByTestId("sentence-item-1")
    await expect(firstSentence).toBeVisible()
    await expect(secondSentence).toBeVisible()

    // Click line 2
    await secondSentence.click()

    // Sentences must NOT unmount or show empty state preparing card
    await expect(page.locator(".now-reading-empty-sentences")).toHaveCount(0)
    await expect(secondSentence).toBeVisible()
    await expect(secondSentence).toHaveAttribute("data-active", "true")
  })
