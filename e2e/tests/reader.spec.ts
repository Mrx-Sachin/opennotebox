import { test, expect } from "@playwright/test"
import { login, openSampleDoc } from "./helpers"

test.describe("reader", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await openSampleDoc(page)
  })

  test("renders the sample PDF text layer", async ({ page }) => {
    await expect(page.getByText("E2E Sample Paper").first()).toBeVisible()
    await expect(page.getByText("This line is for click to read.").first()).toBeVisible()
    await expect(page.locator('input[type="number"][max="2"]')).toBeVisible()
  })

  test("hovering a PDF link paints the indigo preview and shows the URL", async ({ page }) => {
    const probe = await page.evaluate(() => {
      const host = document.getElementById("pdf-page-container-1")
      const layer = host?.querySelector(".textLayer") as HTMLElement | null
      const link = host?.querySelector(".pdf-link") as HTMLElement | null
      if (!host || !layer || !link) return { ok: false as const, reason: "no link overlay" }
      const box = link.getBoundingClientRect()
      layer.dispatchEvent(
        new PointerEvent("pointermove", {
          bubbles: true,
          clientX: box.left + box.width / 2,
          clientY: box.top + box.height / 2,
          pointerId: 1,
          pointerType: "mouse",
        })
      )
      return { ok: true as const }
    })
    expect(probe.ok, probe.ok ? "" : probe.reason).toBe(true)
    await expect.poll(async () =>
      page.locator("#pdf-page-container-1 .textLayer").getAttribute("title")
    ).toContain("https://example.com/e2e-link")
    await expect(page.locator("#pdf-page-container-1 .pdf-link.is-hover")).toHaveCount(1)
    await expect(page.locator("#pdf-page-container-1 .textLayer")).toHaveClass(/link-hover/)
  })

  test("clicking a PDF link follows it (no Ctrl required)", async ({ page }) => {
    const opened = await page.evaluate(() => {
      const host = document.getElementById("pdf-page-container-1")
      const layer = host?.querySelector(".textLayer") as HTMLElement | null
      const link = host?.querySelector(".pdf-link") as HTMLElement | null
      if (!host || !layer || !link) return [] as string[]
      const urls: string[] = []
      const orig = window.open
      window.open = ((url?: string | URL) => {
        urls.push(String(url || ""))
        return null
      }) as typeof window.open
      const box = link.getBoundingClientRect()
      const x = box.left + box.width / 2
      const y = box.top + box.height / 2
      const hit = document.elementFromPoint(x, y)
      hit?.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true, clientX: x, clientY: y })
      )
      window.open = orig
      return urls
    })
    expect(opened).toContain("https://example.com/e2e-link")
  })
})
