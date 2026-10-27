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
  failProgressCount?: number
  progressDelayMs?: number
}

async function setupReaderHarness(page: Page, options: HarnessOptions = {}) {
  let currentUserId = options.initialUserId ?? 101
  let documents = options.initialDocs
    ? options.initialDocs.map((item) => ({ ...item }))
    : createDocs(currentUserId)
  let failProgressRemaining = options.failProgressCount ?? 0
  const progressDelayMs = options.progressDelayMs ?? 0
  const startedProgress: Array<{ docId: number; page: number }> = []
  const recordedProgress: Array<{ docId: number; page: number; userId: number }> = []

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
      startedProgress.push({ docId, page: data.page })
      if (progressDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, progressDelayMs))
      }
      if (failProgressRemaining > 0) {
        failProgressRemaining--
        return route.fulfill({ status: 500, json: { detail: "Progress write failed" } })
      }
      recordedProgress.push({ docId, page: data.page, userId: currentUserId })
      const found = documents.find((entry) => entry.id === docId && entry.user_id === currentUserId)
      if (found) {
        found.last_read_page = data.page
        found.last_read_at = new Date().toISOString()
        return route.fulfill({ json: found })
      }
      return route.fulfill({ status: 404, json: { detail: "Not found" } })
    }

    if (url.pathname.endsWith("/preview")) {
      return route.fulfill({ status: 204 })
    }

    return route.fulfill({ json: [] })
  })

  return {
    getStartedProgress: () => startedProgress,
    getRecordedProgress: () => recordedProgress,
    getDocs: () => documents,
    setUserId: (nextUserId: number) => {
      currentUserId = nextUserId
      documents = createDocs(nextUserId)
    },
    setFailProgressRemaining: (count: number) => {
      failProgressRemaining = count
    },
  }
}

test.describe("reading progress hardening", () => {
  test("per-book last viewed page survives fast A-B-A switching", async ({ page }) => {
    const harness = await setupReaderHarness(page)
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await expect(pageInput).toHaveValue("1")

    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")
    await expect(page.locator("#pdf-page-container-2 canvas").first()).toBeVisible()

    await page.getByRole("button", { name: "QA Research Notes.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61$/)
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("1")

    await page.getByRole("button", { name: "QA Notebook Guide.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/60$/)
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("2")
    await expect(page.locator("#pdf-page-container-2 canvas").first()).toBeVisible()

    await expect.poll(() => harness.getRecordedProgress().some((entry) => entry.docId === 60 && entry.page === 2)).toBe(true)
  })

  test("clicking same book in sidebar does not reset reading progress", async ({ page }) => {
    await setupReaderHarness(page)
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")

    await page.getByRole("button", { name: "QA Notebook Guide.pdf" }).click()
    await expect(pageInput).toHaveValue("2")
    await expect(page.locator("#pdf-page-container-2 canvas").first()).toBeVisible()
  })

  test("cached sidebar reflects progress after debounce", async ({ page }) => {
    const harness = await setupReaderHarness(page)
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")

    await expect.poll(() => harness.getRecordedProgress().some((entry) => entry.docId === 60 && entry.page === 2)).toBe(true)

    await page.getByRole("button", { name: "QA Research Notes.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61$/)
    await expect(page.getByRole("button", { name: "QA Notebook Guide.pdf" })).toContainText("p.2")

    await page.getByRole("button", { name: "QA Notebook Guide.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/60$/)
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("2")
  })

  test("refresh with queued save flushes progress and restores saved page", async ({ page }) => {
    const harness = await setupReaderHarness(page, { progressDelayMs: 800 })
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await expect(pageInput).toHaveValue("1")

    await expect.poll(() => harness.getStartedProgress().some((entry) => entry.docId === 60 && entry.page === 1)).toBe(true)

    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")

    await page.reload()
    await expect(page.locator("#pdf-page-container-2 canvas").first()).toBeVisible()
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("2")

    await expect.poll(() => harness.getRecordedProgress().some((entry) => entry.docId === 60 && entry.page === 2)).toBe(true)
    await expect.poll(() => harness.getDocs().find((entry) => entry.id === 60)?.last_read_page).toBe(2)
  })

  test("deliberate page 1 is preserved and saved after reading ahead", async ({ page }) => {
    const initialDocs = createDocs(101)
    initialDocs[0].last_read_page = 2
    initialDocs[0].last_read_at = "2026-10-08T00:00:00Z"

    const harness = await setupReaderHarness(page, { initialDocs })
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-2 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await expect(pageInput).toHaveValue("2")

    await pageInput.fill("1")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("1")
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()

    await page.getByRole("button", { name: "QA Research Notes.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61$/)

    await page.getByRole("button", { name: "QA Notebook Guide.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/60$/)
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("1")

    await expect.poll(() => harness.getRecordedProgress().some((entry) => entry.docId === 60 && entry.page === 1)).toBe(true)
  })

  test("failed save does not corrupt reader or prevent subsequent successful saves", async ({ page }) => {
    const harness = await setupReaderHarness(page, { failProgressCount: 1 })
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")

    await page.getByRole("button", { name: "QA Research Notes.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61$/)
    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()

    const notesPageInput = page.locator('input[type="number"][max="2"]')
    await expect(notesPageInput).toHaveValue("1")
    await notesPageInput.fill("2")
    await notesPageInput.press("Enter")
    await expect(notesPageInput).toHaveValue("2")

    await page.getByRole("button", { name: "QA Notebook Guide.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/60$/)
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("2")

    await expect.poll(() => harness.getRecordedProgress().some((entry) => entry.docId === 61 && entry.page === 2)).toBe(true)
  })

  test("reading progress remains scoped to authenticated account", async ({ page }) => {
    const harness = await setupReaderHarness(page, { initialUserId: 101 })
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    const pageInput = page.locator('input[type="number"][max="2"]')
    await pageInput.fill("2")
    await pageInput.press("Enter")
    await expect(pageInput).toHaveValue("2")

    await page.getByRole("button", { name: "QA Research Notes.pdf" }).click()
    await expect(page).toHaveURL(/\/dashboard\/read\/61$/)

    harness.setUserId(102)
    await page.evaluate(() => localStorage.setItem("token", "test-token-102"))
    await page.goto("/dashboard/read/60")

    await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
    await expect(page.locator('input[type="number"][max="2"]')).toHaveValue("1")
  })
})
