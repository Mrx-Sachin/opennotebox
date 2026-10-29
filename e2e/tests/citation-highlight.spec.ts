import { test, expect, type Page, type Route } from "@playwright/test"
import type { Citation, Document, Message, User } from "../../frontend/src/services/types"

const sampleDoc1: Document = {
  id: 1,
  user_id: 71,
  title: "citation-evidence.pdf",
  format: "pdf",
  file_size: 8192,
  num_pages: 2,
  processing_status: "completed",
  created_at: "2026-01-01T00:00:00Z",
  last_read_page: 1,
  last_read_position: null,
  last_read_at: null,
}

const sampleDoc2: Document = {
  id: 2,
  user_id: 71,
  title: "e2e-sample.pdf",
  format: "pdf",
  file_size: 4096,
  num_pages: 2,
  processing_status: "completed",
  created_at: "2026-01-01T00:00:00Z",
  last_read_page: 1,
  last_read_position: null,
  last_read_at: null,
}

const testUser: User = {
  id: 71,
  username: "e2e.tester",
  is_admin: false,
  created_at: "2026-01-01T00:00:00Z",
}

function setupMockRoutes(page: Page, messages: Message[]) {
  return page.route("**/api/**", async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())

    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: testUser })
    } else if (url.pathname === "/api/documents") {
      await route.fulfill({ json: [sampleDoc1, sampleDoc2] })
    } else if (url.pathname === "/api/documents/1") {
      await route.fulfill({ json: sampleDoc1 })
    } else if (url.pathname === "/api/documents/2") {
      await route.fulfill({ json: sampleDoc2 })
    } else if (url.pathname === "/api/documents/1/file") {
      await route.fulfill({
        path: "fixtures/citation-evidence.pdf",
        contentType: "application/pdf",
      })
    } else if (url.pathname === "/api/documents/2/file") {
      await route.fulfill({
        path: "fixtures/e2e-sample.pdf",
        contentType: "application/pdf",
      })
    } else if (url.pathname === "/api/settings") {
      await route.fulfill({
        json: {
          llm: {
            active: "omniroute",
            backends: [{ name: "omniroute", base_url: "http://mock/v1", model: "mock" }],
          },
          tts: { default_voice: "test" },
          export: { enabled: false },
        },
      })
    } else if (url.pathname === "/api/settings/llm/health") {
      await route.fulfill({
        json: { active: "omniroute", backends: [{ name: "omniroute", status: "healthy" }] },
      })
    } else if (url.pathname === "/api/chat/sessions" && request.method() === "GET") {
      await route.fulfill({
        json: [
          {
            id: 100,
            user_id: testUser.id,
            title: "Citation Test Chat",
            document_ids: [1, 2],
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      })
    } else if (url.pathname.match(/\/api\/chat\/sessions\/\d+\/messages/)) {
      await route.fulfill({ json: messages })
    } else {
      await route.fulfill({ json: [] })
    }
  })
}

test.describe("citation highlighting and provenance", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("token", "e2e-citation-token"))
  })

  test("legacy citation without evidence navigates to page but does NOT highlight wrong caption", async ({ page }) => {
    const citation: Citation = {
      n: 1,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "Figure 8. Zero-shot Whisper performance scales reliably across tasks and languages with increasing model size.",
      evidence: null,
    }

    const message: Message = {
      id: 1,
      conversation_id: 100,
      role: "assistant",
      content: "Whisper performance scales reliably with dataset size [1].",
      citations: [citation],
      token_count: 20,
    }

    await setupMockRoutes(page, [message])

    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    const citationChip = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(citationChip).toBeVisible()
    await citationChip.click()

    await expect(page.locator("#pdf-page-container-2")).toBeVisible({ timeout: 10_000 })
    await expect(page.locator("#pdf-page-container-2 .textLayer span").first()).toBeVisible({ timeout: 10_000 })

    // Legacy citation has evidence: null -> must NOT highlight any passage
    const flashes = page.locator("#pdf-page-container-2 .citation-flash")
    expect(await flashes.count()).toBe(0)
  })

  test("exact evidence highlights supporting body paragraph and NOT figure caption", async ({ page }, testInfo) => {
    const citation: Citation = {
      n: 1,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "Figure 8. Zero-shot Whisper performance scales reliably across tasks and languages with increasing model size.",
      evidence: {
        exact: "All increases in the dataset size result in improved performance on all tasks, although we see significant variability in improvement rates across tasks and sizes.",
      },
    }

    const message: Message = {
      id: 2,
      conversation_id: 100,
      role: "assistant",
      content: "Dataset scaling drives improvements on all speech tasks [1].",
      citations: [citation],
      token_count: 25,
    }

    await setupMockRoutes(page, [message])

    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    const citationChip = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(citationChip).toBeVisible()
    await citationChip.click()

    await expect(page.locator("#pdf-page-container-2")).toBeVisible({ timeout: 10_000 })

    // Supporting paragraph text must have citation-flash
    const bodySpan = page.locator("#pdf-page-container-2 .textLayer span", { hasText: "All increases in the dataset" })
    await expect(bodySpan).toHaveClass(/citation-flash/, { timeout: 10_000 })

    // Figure 8 caption text must NOT have citation-flash
    const figureSpan = page.locator("#pdf-page-container-2 .textLayer span", { hasText: "Figure 8" })
    await expect(figureSpan).not.toHaveClass(/citation-flash/)

    // Move cursor away to clear hover preview card and ensure paragraph is cleanly centered
    await page.mouse.move(0, 0)
    await bodySpan.scrollIntoViewIfNeeded()

    // Capture visual verification screenshot WHILE highlight is active into testInfo output
    await page.screenshot({ path: testInfo.outputPath("citation-body-highlight.png") })
  })

  test("cross-document citation navigates to target document and highlights evidence", async ({ page }) => {
    const citation: Citation = {
      n: 1,
      doc_id: 2, // Points to Doc 2
      doc_title: "e2e-sample.pdf",
      page: 1,
      excerpt: "There are genuinely good agentic frameworks available today.",
      evidence: {
        exact: "There are genuinely good agentic frameworks available today.",
      },
    }

    const message: Message = {
      id: 3,
      conversation_id: 100,
      role: "assistant",
      content: "External reference from doc 2 [1].",
      citations: [citation],
      token_count: 15,
    }

    await setupMockRoutes(page, [message])
    // Start on Doc 1
    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    // Click citation for Doc 2
    const citationChip = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(citationChip).toBeVisible()
    await citationChip.click()

    // Must navigate to Doc 2 URL
    await page.waitForURL("**/dashboard/read/2", { timeout: 10_000 })

    // Wait for Doc 2 content to render
    await expect(page.getByText("E2E Sample Paper").first()).toBeVisible({ timeout: 15_000 })

    // The quoted span on Doc 2 must have citation-flash
    const doc2Span = page.locator("#pdf-page-container-1 .textLayer span", { hasText: "There are genuinely good agentic frameworks" })
    await expect(doc2Span).toHaveClass(/citation-flash/, { timeout: 10_000 })
  })

  test("ambiguous repeated quote on same page fails honestly with toast and no highlight", async ({ page }) => {
    const citation: Citation = {
      n: 1,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "Identical repeated passage",
      evidence: {
        exact: "Identical repeated passage on page two for ambiguity testing.",
      },
    }

    const message: Message = {
      id: 4,
      conversation_id: 100,
      role: "assistant",
      content: "Ambiguous claim [1].",
      citations: [citation],
      token_count: 15,
    }

    await setupMockRoutes(page, [message])

    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    const citationChip = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(citationChip).toBeVisible()
    await citationChip.click()

    await expect(page.locator("#pdf-page-container-2")).toBeVisible({ timeout: 10_000 })
    await expect(page.locator("#pdf-page-container-2 .textLayer span").first()).toBeVisible({ timeout: 10_000 })

    const flashes = page.locator("#pdf-page-container-2 .citation-flash")
    expect(await flashes.count()).toBe(0)

    await expect(page.getByText(/passage ambiguous/i)).toBeVisible({ timeout: 5_000 })
  })

  test("near-match with missing punctuation/signs fails honestly without highlighting", async ({ page }) => {
    const citation: Citation = {
      n: 1,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "We evaluate models with 1 and +1 score adjustments across 13 training phases.",
      evidence: {
        // PDF has "-1" and "1-3", test near-match "1" and "13"
        exact: "We evaluate models with 1 and +1 score adjustments across 13 training phases.",
      },
    }

    const message: Message = {
      id: 5,
      conversation_id: 100,
      role: "assistant",
      content: "Numeric test claim [1].",
      citations: [citation],
      token_count: 15,
    }

    await setupMockRoutes(page, [message])

    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    const citationChip = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(citationChip).toBeVisible()
    await citationChip.click()

    await expect(page.locator("#pdf-page-container-2")).toBeVisible({ timeout: 10_000 })
    await expect(page.locator("#pdf-page-container-2 .textLayer span").first()).toBeVisible({ timeout: 10_000 })

    const flashes = page.locator("#pdf-page-container-2 .citation-flash")
    expect(await flashes.count()).toBe(0)

    await expect(page.getByText(/passage not found/i)).toBeVisible({ timeout: 5_000 })
  })

  test("multiple citations on same page highlight their respective passages", async ({ page }) => {
    const citation1: Citation = {
      n: 1,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "All increases in the dataset size",
      evidence: {
        exact: "All increases in the dataset size result in improved performance on all tasks, although we see significant variability in improvement rates across tasks and sizes.",
      },
    }

    const citation2: Citation = {
      n: 2,
      doc_id: 1,
      doc_title: "citation-evidence.pdf",
      page: 2,
      excerpt: "Multitask learning provides robust transfer",
      evidence: {
        exact: "Multitask learning provides robust transfer across speech recognition and translation benchmarks with minimal fine-tuning required.",
      },
    }

    const message: Message = {
      id: 6,
      conversation_id: 100,
      role: "assistant",
      content: "First claim [1] and second claim [2].",
      citations: [citation1, citation2],
      token_count: 30,
    }

    await setupMockRoutes(page, [message])

    await page.goto("/dashboard/read/1")
    await expect(page.locator("#pdf-page-container-1")).toBeVisible({ timeout: 15_000 })

    // Click citation [1]
    const chip1 = page.getByRole("button", { name: "1", exact: true }).first()
    await expect(chip1).toBeVisible()
    await chip1.click()

    const bodySpan1 = page.locator("#pdf-page-container-2 .textLayer span", { hasText: "All increases in the dataset" })
    await expect(bodySpan1).toHaveClass(/citation-flash/, { timeout: 10_000 })

    // Hover chip 2 to verify DOM chip stability across hover/tooltip
    const chip2 = page.getByRole("button", { name: "2", exact: true }).first()
    await chip2.hover()
    await expect(chip2).toBeVisible()

    // Plain click citation [2] (no force, no arbitrary delay)
    await chip2.click()

    const bodySpan2 = page.locator("#pdf-page-container-2 .textLayer span", { hasText: "Multitask learning provides" })
    await expect(bodySpan2).toHaveClass(/citation-flash/, { timeout: 10_000 })
    // Earlier citation flash on span 1 is retired
    await expect(bodySpan1).not.toHaveClass(/citation-flash/)
  })
})
