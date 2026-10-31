import { expect, test } from "@playwright/test"
import { spawn, type ChildProcess } from "node:child_process"
import http from "node:http"
import net from "node:net"
import path from "node:path"
import fs from "node:fs"
import { mockLibrary, libraryDocuments } from "./library-fixture"

let serverProcess: ChildProcess | null = null
let serverPort = 0
let serverUrl = ""

async function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer()
    s.listen(0, "127.0.0.1", () => {
      const addr = s.address()
      if (addr && typeof addr === "object") {
        const port = addr.port
        s.close(() => resolve(port))
      } else {
        reject(new Error("Unable to obtain free loopback port"))
      }
    })
  })
}

async function waitForServer(url: string, timeoutMs = 20_000): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const ready = await new Promise<boolean>((resolve) => {
      const req = http.get(`${url}/login`, (res) => {
        res.resume()
        resolve(res.statusCode === 200)
      })
      req.setTimeout(1000, () => req.destroy())
      req.on("error", () => resolve(false))
    })
    if (ready) return
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`Frontend server failed to start at ${url} within ${timeoutMs}ms`)
}

test.describe.configure({ mode: "serial" })

test.describe("Resource improvements: non-PDF lightweight reader and 8s polling", () => {
  test.beforeAll(async () => {
    serverPort = await getFreePort()
    serverUrl = `http://127.0.0.1:${serverPort}`
    const frontendDir = path.resolve(__dirname, "../../frontend")

    serverProcess = spawn(
      "./node_modules/.bin/next",
      ["start", "-p", String(serverPort), "-H", "127.0.0.1"],
      {
        cwd: frontendDir,
        stdio: "pipe",
        env: { ...process.env, PORT: String(serverPort) },
      }
    )

    await waitForServer(serverUrl)

    const screenshotDir = path.resolve(__dirname, "../../_scratch/resource-browser")
    if (!fs.existsSync(screenshotDir)) {
      fs.mkdirSync(screenshotDir, { recursive: true })
    }
  })

  test.afterAll(async () => {
    if (serverProcess) {
      serverProcess.kill("SIGTERM")
      serverProcess = null
    }
  })

  test("non-PDF reader requests lightweight query and renders content, headings, and navigation", async ({ page }) => {
    await mockLibrary(page)

    const capturedPagesQueries: URLSearchParams[] = []

    await page.route("**/api/documents/2", async (route) => {
      return route.fulfill({
        json: {
          ...libraryDocuments[1],
          id: 2,
          format: "epub",
          num_pages: 3,
          processing_status: "completed",
        },
      })
    })

    await page.route("**/api/documents/2/pages*", async (route) => {
      const url = new URL(route.request().url())
      capturedPagesQueries.push(url.searchParams)

      return route.fulfill({
        json: [
          {
            id: 201,
            document_id: 2,
            page_number: 1,
            content: "Foundational principles of empirical observation and systematic analysis.\n\nDeveloping reproducible research hypotheses.",
            headings: [{ title: "Chapter 1: Research Foundations", level: 1 }],
          },
          {
            id: 202,
            document_id: 2,
            page_number: 2,
            content: "Quantitative experimental controls and comparative measurement.\n\nSampling frameworks across varied distributions.",
            headings: [{ title: "Chapter 2: Quantitative Design", level: 1 }],
          },
          {
            id: 203,
            document_id: 2,
            page_number: 3,
            content: "Thematic synthesis, cross-validation, and peer verification protocols.",
            headings: [{ title: "Chapter 3: Synthesis and Validation", level: 1 }],
          },
        ],
      })
    })

    await page.route("**/api/documents?*", async (route) => {
      return route.fulfill({
        json: [
          {
            id: 2,
            user_id: 101,
            notebook_id: null,
            title: "Research Methods.epub",
            format: "epub",
            num_pages: 3,
            processing_status: "completed",
          },
        ],
      })
    })

    await page.goto(`${serverUrl}/dashboard/read/2`)

    await page.waitForSelector("#text-page-container-1")

    expect(capturedPagesQueries.length).toBeGreaterThan(0)
    expect(capturedPagesQueries[0].get("lightweight")).toBe("true")

    const heading1 = page.getByRole("heading", { name: "Chapter 1: Research Foundations" })
    await expect(heading1).toBeVisible()
    const content1 = page.getByText("Developing reproducible research hypotheses.")
    await expect(content1).toBeVisible()

    const counter = page.getByText(/^\d+ \/ \d+$/)
    await expect(counter).toHaveText("1 / 3")

    await page.locator("button[title=\"Table of contents\"]").click()
    await expect(page.getByText("Chapters", { exact: true })).toBeVisible()

    await page.getByRole("button", { name: /Chapter 2: Quantitative Design/ }).click()
    await expect(counter).toHaveText("2 / 3")
    await expect(page.getByRole("heading", { name: "Chapter 2: Quantitative Design" })).toBeVisible()
    await expect(page.getByText("Quantitative experimental controls and comparative measurement.")).toBeVisible()

    const navContainer = counter.locator("..")
    const prevButton = navContainer.locator("button").first()
    const nextButton = navContainer.locator("button").last()

    await prevButton.click()
    await expect(counter).toHaveText("1 / 3")
    await expect(heading1).toBeVisible()

    await nextButton.click()
    await expect(counter).toHaveText("2 / 3")

    await nextButton.click()
    await expect(counter).toHaveText("3 / 3")
    await expect(page.getByRole("heading", { name: "Chapter 3: Synthesis and Validation" })).toBeVisible()
    await expect(page.getByText("Thematic synthesis, cross-validation, and peer verification protocols.")).toBeVisible()

    const screenshotPath = path.resolve(__dirname, "../../_scratch/resource-browser/non-pdf-reader-lightweight.png")
    await page.screenshot({ path: screenshotPath, fullPage: true })
  })

  test("useDocuments polling: no 2s repeat, occurs at 8s, updates completed UI, and stops thereafter", async ({ page }) => {
    test.setTimeout(50_000)

    await mockLibrary(page)

    await page.route("**/api/documents/2", async (route) => {
      return route.fulfill({
        json: {
          ...libraryDocuments[1],
          id: 2,
          format: "epub",
          num_pages: 1,
          processing_status: "completed",
        },
      })
    })

    await page.route("**/api/documents/2/pages*", async (route) => {
      return route.fulfill({
        json: [
          {
            id: 201,
            document_id: 2,
            page_number: 1,
            content: "Reader page content for polling test.",
            headings: [{ title: "Polling Verification", level: 1 }],
          },
        ],
      })
    })

    const pollLog: Array<{ count: number; timestamp: number }> = []
    let pollIndex = 0

    await page.route("**/api/documents?*", async (route) => {
      pollIndex++
      const now = Date.now()
      pollLog.push({ count: pollIndex, timestamp: now })

      const pendingDocStatus = pollIndex === 1 ? "pending" : "completed"

      return route.fulfill({
        json: [
          {
            id: 2,
            user_id: 101,
            notebook_id: null,
            title: "Research Methods.epub",
            format: "epub",
            num_pages: 1,
            processing_status: "completed",
          },
          {
            id: 30,
            user_id: 101,
            notebook_id: null,
            title: "Async Data Pipeline.pdf",
            format: "pdf",
            num_pages: 4,
            processing_status: pendingDocStatus,
          },
        ],
      })
    })

    await page.goto(`${serverUrl}/dashboard/read/2`)
    await page.waitForSelector("#text-page-container-1")

    expect(pollLog.length).toBe(1)
    const initialPollTime = pollLog[0].timestamp

    const pendingDocButton = page.locator("button[title=\"Async Data Pipeline.pdf\"]")
    await expect(pendingDocButton).toBeVisible()
    await expect(pendingDocButton).toContainText("queued")

    const pendingScreenshotPath = path.resolve(__dirname, "../../_scratch/resource-browser/polling-pending-sidebar.png")
    await page.screenshot({ path: pendingScreenshotPath, fullPage: true })

    await page.waitForTimeout(3500)
    expect(pollLog.length).toBe(1)

    await expect.poll(() => pollLog.length, {
      message: "Expected second poll at 8s interval",
      timeout: 10_000,
      intervals: [500],
    }).toBe(2)

    const secondPollTime = pollLog[1].timestamp
    const pollIntervalMs = secondPollTime - initialPollTime
    expect(pollIntervalMs).toBeGreaterThanOrEqual(7200)
    expect(pollIntervalMs).toBeLessThanOrEqual(9500)

    await expect(pendingDocButton).not.toContainText("queued")
    await expect(pendingDocButton.locator("span", { hasText: "queued" })).toHaveCount(0)

    const screenshotPath = path.resolve(__dirname, "../../_scratch/resource-browser/polling-completed-sidebar.png")
    await page.screenshot({ path: screenshotPath, fullPage: true })

    await page.waitForTimeout(9500)
    expect(pollLog.length).toBe(2)
  })
})
