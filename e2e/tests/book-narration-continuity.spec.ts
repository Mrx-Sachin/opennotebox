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

interface HarnessOptions {
  delayDoc60Ms?: number
  customSentences?: Record<number, string[]>
}

async function setupReaderHarness(page: Page, options: HarnessOptions = {}) {
  const currentUserId = 101
  const documents = createDocs(currentUserId)
  const releaseEvents: Array<{ sessionId?: string; cacheIds?: number[]; allSession?: boolean }> = []
  const streamRequests: Array<{ text: string; sessionId?: string }> = []
  const pageRequests: Array<{ docId: number; pageNum: number }> = []

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
      if (docId === 60 && options.delayDoc60Ms) {
        await new Promise((r) => setTimeout(r, options.delayDoc60Ms))
      }
      const found = documents.find((entry) => entry.id === docId && entry.user_id === currentUserId)
      if (found) return route.fulfill({ json: found })
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    const fileMatch = url.pathname.match(/^\/api\/documents\/(\d+)\/file$/)
    if (fileMatch) {
      return route.fulfill({
        path: "fixtures/e2e-sample.pdf",
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
      pageRequests.push({ docId, pageNum })
      const sentences = options.customSentences?.[pageNum] || [
        `First narrative sentence of document ${docId} page ${pageNum}.`,
        `Second sequential sentence elaborating details clearly.`,
        `Third concluding thought for this section.`,
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
    pageRequests,
  }
}

test.describe("Continuous Source Book Narration", () => {
  test("continuous narration persists across library, shared library, and other document", async ({ page }) => {
    const harness = await setupReaderHarness(page, { customSentences: { 5: ["The document conclusion ends here."] } })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const playBtn = page.getByRole("button", { name: "Play (Space)", exact: true })
    await expect(playBtn).toBeVisible()
    await playBtn.click()

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const initialSessionId = harness.streamRequests[0].sessionId
    expect(initialSessionId).toBeDefined()

    await expect.poll(async () => {
      return page.evaluate(() => {
        const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
        return audio ? !audio.paused : false
      })
    }).toBe(true)

    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)

    const bottomBar = page.getByTestId("book-bottom-bar")
    await expect(bottomBar).toBeVisible()
    await expect(page.getByTestId("book-bottom-title")).toHaveText("QA Notebook Guide.pdf")
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 1 of 5")
    await expect(page.getByTestId("book-bottom-title")).toHaveText("QA Notebook Guide.pdf")

    const audioOnLibrary = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(audioOnLibrary).toBe(true)

    await page.getByRole("link", { name: "Shared library" }).first().click()
    await expect(page).toHaveURL(/\/dashboard\/shared/)

    await expect(bottomBar).toBeVisible()
    const audioOnShared = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(audioOnShared).toBe(true)

    await page.getByRole("link", { name: "Notebooks" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)

    await page.getByRole("link", { name: "Open Unfiled documents" }).click()
    await expect(page).toHaveURL(/\/dashboard\?unfiled=true/)

    await page.getByRole("link", { name: "Open QA Research Notes", exact: true }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await expect(bottomBar).toBeVisible()
    await expect(page.getByTestId("book-bottom-title")).toHaveText("QA Notebook Guide.pdf")

    const audioOnDoc61 = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(audioOnDoc61).toBe(true)

    expect(
      harness.releaseEvents.some((ev) => ev.sessionId === initialSessionId && ev.allSession === true)
    ).toBe(false)
  })

  test("foreign PDF shows no highlight, selection does not interrupt, and page advance does not mutate foreign viewer", async ({ page }) => {
    const harness = await setupReaderHarness(page, { customSentences: { 5: ["The document conclusion ends here."] } })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    await page.getByRole("button", { name: /QA Research Notes/ }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const activeHighlights = page.locator(".tts-sentence-active")
    await expect(activeHighlights).toHaveCount(0)

    await page.evaluate(() => {
      const span = document.querySelector("#pdf-page-container-1 .textLayer span")
      if (span) {
        const range = document.createRange()
        range.selectNodeContents(span)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(range)
        document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, clientX: 100, clientY: 100 }))
      }
    })

    const audioStillPlaying = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(audioStillPlaying).toBe(true)

    const pageInput = page.locator('input[type="number"]').first()
    await expect(pageInput).toHaveValue("1")

    await page.getByTestId("book-bottom-next-page").click()
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 2 of 5")

    await expect(pageInput).toHaveValue("1")
    await expect(page).toHaveURL(/\/dashboard\/read\/61/)
  })

  test("different source and view bounds respects source numPages and natural end cleans up", async ({ page }) => {
    const harness = await setupReaderHarness(page, { customSentences: { 5: ["The document conclusion ends here."] } })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const sessionId = harness.streamRequests[0].sessionId

    await page.getByRole("button", { name: /QA Research Notes/ }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const nextBtn = page.getByTestId("book-bottom-next-page")
    await nextBtn.click()
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 2 of 5")

    await nextBtn.click()
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 3 of 5")

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 5, 0)
      }
    })
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 5 of 5")
    await expect.poll(async () => {
      return page.evaluate(() => {
        const audio = (window as any).__capturedAudioElement
        return audio ? !audio.paused : false
      })
    }).toBe(true)

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.onEnded()
      } else {
        const audio = (window as any).__capturedAudioElement
        audio.dispatchEvent(new Event("ended"))
      }
    })

    await expect.poll(() =>
      harness.releaseEvents.some((ev) => ev.allSession === true)
    ).toBe(true)

    await expect(page.getByTestId("book-bottom-bar")).toHaveCount(0)
  })

  test("now reading view expansion, keyboard navigation, and open in book jump", async ({ page }) => {
    const harness = await setupReaderHarness(page, { customSentences: { 5: ["The document conclusion ends here."] } })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)

    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)
    const bottomBar = page.getByTestId("book-bottom-bar")
    await expect(bottomBar).toBeVisible()
    await page.getByTestId("book-bottom-expand-btn").click()
    const modal = page.getByTestId("now-reading-modal")
    await expect(modal).toBeVisible()
    await expect(page.getByTestId("now-reading-title")).toHaveText("QA Notebook Guide.pdf")

    const firstSentence = page.getByTestId("sentence-item-0")
    await expect(firstSentence).toBeVisible()

    await page.keyboard.press("Escape")
    await expect(modal).toHaveCount(0)
    await expect(bottomBar).toBeVisible()

    const audioAfterEscape = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio ? !audio.paused : false
    })
    expect(audioAfterEscape).toBe(true)

    await page.getByTestId("book-bottom-next-page").click()
    await expect(page.getByTestId("book-bottom-page")).toHaveText("Page 2 of 5")

    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)

    await page.getByTestId("book-bottom-title").click()
    await expect(modal).toBeVisible()
    await expect(page.getByTestId("now-reading-page")).toHaveText("Page 2 of 5")

    await page.getByTestId("now-reading-open-book").click()
    await expect(page).toHaveURL(/\/dashboard\/read\/60/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const pageInput = page.locator('input[type="number"]').first()
    await expect(pageInput).toHaveValue("2")
  })

  test("navigation during preparing metadata resolves and plays seamlessly", async ({ page }) => {
    const harness = await setupReaderHarness(page, { delayDoc60Ms: 600 })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()

    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)

    const bottomBar = page.getByTestId("book-bottom-bar")
    await expect(bottomBar).toBeVisible({ timeout: 15000 })
    await expect(page.getByTestId("book-bottom-title")).toHaveText("QA Notebook Guide.pdf")

    await expect.poll(async () => {
      return page.evaluate(() => {
        const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
        return audio ? !audio.paused : false
      })
    }).toBe(true)
  })

  test("explicit stop and audio takeover trigger immediate session cleanup", async ({ page }) => {
    const harness = await setupReaderHarness(page, { customSentences: { 5: ["The document conclusion ends here."] } })

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const session1 = harness.streamRequests[0].sessionId

    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page).toHaveURL(/\/dashboard$/)

    await page.getByTestId("book-bottom-stop-btn").click()
    await expect(page.getByTestId("book-bottom-bar")).toHaveCount(0)

    await expect.poll(() =>
      harness.releaseEvents.some((ev) => ev.allSession === true)
    ).toBe(true)

    const audioStopped = await page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio == null || audio.paused || !audio.src
    })
    expect(audioStopped).toBe(true)

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.skipPage(1)
      }
    })
    await expect(page.getByTestId("book-bottom-bar")).toHaveCount(0)

    await page.getByRole("link", { name: "Open Unfiled documents" }).click()
    await expect(page).toHaveURL(/\/dashboard\?unfiled=true/)

    await page.getByRole("link", { name: "Open QA Research Notes", exact: true }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.getByRole("button", { name: "Play (Space)", exact: true }).click()
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(1)
    await page.getByRole("link", { name: "Library home" }).click()
    await expect(page.getByTestId("book-bottom-bar")).toBeVisible()
    await expect(page.getByTestId("book-bottom-title")).toHaveText("QA Research Notes.pdf")
  })
})
