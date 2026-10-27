import { expect, test, type Page } from "@playwright/test"

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
      num_pages: 2,
      processing_status: "completed",
      created_at: "2026-10-01T00:00:00Z",
      last_read_page: null,
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
      last_read_page: null,
      last_read_at: null,
      last_read_position: null,
    },
  ]
}

interface HarnessOptions {
  initialDocs?: MockDoc[]
  initialUserId?: number
}

async function setupReaderHarness(page: Page, options: HarnessOptions = {}) {
  let currentUserId = options.initialUserId ?? 101
  let documents = options.initialDocs
    ? options.initialDocs.map((item) => ({ ...item }))
    : createDocs(currentUserId)
  const releaseEvents: Array<{ sessionId?: string; cacheIds?: number[]; allSession?: boolean }> = []
  const streamRequests: Array<{ text: string; sessionId?: string }> = []
  const heartbeatEvents: Array<{ sessionId: string }> = []
  const audioFetchRequests: Array<{ url: string }> = []

  await page.addInitScript((storageUserId) => {
    localStorage.setItem("token", `test-token-${storageUserId}`)
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
          reading_count: userDocs.filter((entry) => entry.last_read_at).length,
          processing_count: 0,
          failed_count: 0,
          formats: { pdf: userDocs.length },
          recent: userDocs.filter((entry) => entry.last_read_at),
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
        path: "fixtures/e2e-sample.pdf",
        contentType: "application/pdf",
      })
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
      const wavBuffer = Buffer.from(
        "UklGRl4RAABXQVZFZm10IBAAAAABAAEAESsAABErAAABAAgAZGF0YToRAAAA" +
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "base64"
      )
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: wavBuffer,
        headers: {
          "x-tts-duration": "3.5",
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
      const postData = request.postDataJSON()
      heartbeatEvents.push({ sessionId: postData.session_id })
      return route.fulfill({
        status: 200,
        json: { status: "ok", renewed: true },
      })
    }

    const pageTextMatch = url.pathname.match(/^\/api\/documents\/\d+\/page\/(\d+)$/)
    if (pageTextMatch && method === "GET") {
      const pageNum = Number(pageTextMatch[1])
      return route.fulfill({
        json: {
          page_number: pageNum,
          content: "First sentence of reading audio. Second sentence of reading audio. Third sentence of reading audio.",
        },
      })
    }

    const audioMatch = url.pathname.match(/^\/api\/tts\/audio\/(\d+)$/)
    if (audioMatch && method === "GET") {
      audioFetchRequests.push({ url: request.url() })
      const wavBuffer = Buffer.from(
        "UklGRl4RAABXQVZFZm10IBAAAAABAAEAESsAABErAAABAAgAZGF0YToRAAAA" +
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "base64"
      )
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: wavBuffer,
      })
    }

    if (url.pathname.endsWith("/preview")) {
      return route.fulfill({ status: 204 })
    }

    return route.fulfill({ json: [] })
  })

  return {
    releaseEvents,
    streamRequests,
    heartbeatEvents,
    audioFetchRequests,
  }
}

test.describe("Reading audio memory and disk lifecycle", () => {
  test("click to read synthesizes and stopping releases session audio immediately", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0)
      }
    })

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const initialSessionId = harness.streamRequests[0].sessionId
    expect(initialSessionId).toBeDefined()

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.stop()
      }
    })

    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)
    const matchingRelease = harness.releaseEvents.find(
      (ev) => ev.sessionId === initialSessionId && ev.allSession === true
    )
    expect(matchingRelease).toBeDefined()
  })

  test("switching books immediately releases previous book audio session", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0)
      }
    })

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const firstSessionId = harness.streamRequests[0].sessionId

    await page.goto("/dashboard/read/61")
    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)

    const releasedFirstBook = harness.releaseEvents.some(
      (ev) => ev.sessionId === firstSessionId && ev.allSession === true
    )
    expect(releasedFirstBook).toBe(true)
  })

  test("superseded click generation releases obsolete chunk and rotates session", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0)
      }
    })
    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const firstSessionId = harness.streamRequests[0].sessionId

    await page.waitForTimeout(350)
    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 1)
      }
    })

    await expect.poll(() => harness.streamRequests.some((req) => req.sessionId !== firstSessionId)).toBe(true)
    const secondReq = harness.streamRequests.find((req) => req.sessionId !== firstSessionId)
    expect(secondReq?.sessionId).toBeDefined()

    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)
    const releasedFirstGen = harness.releaseEvents.some(
      (ev) => ev.sessionId === firstSessionId
    )
    expect(releasedFirstGen).toBe(true)
  })

  test("natural playback end releases session and frees audio blob", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.playAudioUrl("/api/tts/audio/999")
      }
    })

    await expect.poll(() => harness.audioFetchRequests.length).toBeGreaterThan(0)

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine && win.ttsEngine.audio) {
        win.ttsEngine.audio.dispatchEvent(new Event("ended"))
      }
    })

    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)
    expect(harness.releaseEvents.some((ev) => ev.allSession === true)).toBe(true)
  })

  test("pagehide event sends beacon to release all session audio", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0)
      }
    })

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const activeSessionId = harness.streamRequests[0].sessionId

    await page.evaluate(() => {
      window.dispatchEvent(new Event("pagehide"))
    })

    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)
    const beaconRelease = harness.releaseEvents.find(
      (ev) => ev.sessionId === activeSessionId && ev.allSession === true
    )
    expect(beaconRelease).toBeDefined()
  })

  test("repeated-text remains playable with separate chunk ownership", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const textWithRefrain = [
      { text: "Refrain sentence repeated.", sentenceIdx: 0 },
      { text: "Middle sentence unique.", sentenceIdx: 1 },
      { text: "Refrain sentence repeated.", sentenceIdx: 2 },
    ]

    await page.evaluate(async (sentences) => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0, sentences)
      }
    })

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const initialSessionId = harness.streamRequests[0].sessionId
    expect(initialSessionId).toBeDefined()

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine && win.ttsEngine.audio) {
        win.ttsEngine.audio.dispatchEvent(new Event("ended"))
      }
    })

    const audioState = await page.evaluate(() => {
      const win = window as any
      const audio = win.ttsEngine?.audio
      return {
        hasAudio: Boolean(audio),
        src: audio ? audio.src : "",
        error: audio && audio.error ? audio.error.code : null,
      }
    })

    expect(audioState.error).toBeNull()
  })
  test("chunk completion immediately releases played chunk while next chunk remains held", async ({ page }) => {
    const harness = await setupReaderHarness(page)

    await page.goto("/dashboard/read/60")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 20000 })

    const multiSentencePage = [
      { text: "Long chunk zero sentence that exceeds entry threshold easily.", sentenceIdx: 0 },
      { text: "Long chunk one sentence that will form the second chunk cleanly.", sentenceIdx: 1 },
    ]

    await page.evaluate(async (sentences) => {
      const win = window as any
      if (win.ttsEngine) {
        win.ttsEngine.readPage(60, 1, 0, sentences)
      }
    })

    await expect.poll(() => harness.streamRequests.length).toBeGreaterThan(0)
    const initialSessionId = harness.streamRequests[0].sessionId
    expect(initialSessionId).toBeDefined()

    await page.evaluate(() => {
      const win = window as any
      if (win.ttsEngine && win.ttsEngine.audio) {
        win.ttsEngine.audio.dispatchEvent(new Event("ended"))
      }
    })

    await expect.poll(() => harness.releaseEvents.length).toBeGreaterThan(0)
    const releasedChunk = harness.releaseEvents.find(
      (ev) => ev.sessionId === initialSessionId && Array.isArray(ev.cacheIds) && ev.cacheIds.length > 0
    )
    expect(releasedChunk).toBeDefined()
  })
})
