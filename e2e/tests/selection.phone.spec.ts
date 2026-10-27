import { test, expect } from "@playwright/test"
import { login } from "./helpers"

test.describe("phone selection", () => {
  test("long-press handles let the user grow the selected region", async ({ page }) => {
    await login(page)

    const notesToggle = page.getByRole("button", { name: "Notes", exact: true })
    if ((await notesToggle.getAttribute("aria-pressed")) === "true") {
      await notesToggle.click()
    }
    const chatToggle = page.getByRole("button", { name: "Assistant", exact: true })
    if ((await chatToggle.getAttribute("aria-pressed")) === "true") {
      await chatToggle.click()
    }

    const lib = page.getByRole("button", { name: "Library", exact: true })
    if ((await lib.getAttribute("aria-pressed")) !== "true") {
      await lib.click()
    }
    await page.getByRole("button", { name: /e2e-sample/ }).click()
    await expect(page.locator("#pdf-page-container-1 .textLayer span").first()).toBeVisible({
      timeout: 20_000,
    })

    const before = await page.evaluate(() => {
      const layer = document.querySelector("#pdf-page-container-1 .textLayer")
      const spans = [...(layer?.querySelectorAll("span") || [])].filter(
        (s) =>
          !s.classList.contains("markedContent") &&
          s.childElementCount === 0 &&
          (s.textContent || "").trim()
      )
      if (spans.length < 2) return null
      const a = spans[0]
      const range = document.createRange()
      range.setStart(a.firstChild || a, 0)
      range.setEnd(a.firstChild || a, Math.min(4, (a.textContent || "").length))
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(range)
      layer?.dispatchEvent(
        new PointerEvent("pointerup", {
          bubbles: true,
          clientX: 200,
          clientY: 200,
          pointerId: 1,
          pointerType: "touch",
          button: 0,
        })
      )
      return sel?.toString() || ""
    })
    expect(before && before.length).toBeGreaterThan(0)

    const bar = page.locator("div.fixed.z-40")
    await expect(bar).toBeVisible()
    const startQuote = await bar.locator("span").first().innerText()

    const endHandle = page.getByRole("button", { name: "Move selection end" })
    await expect(endHandle).toBeVisible()
    await expect(page.getByRole("button", { name: "Move selection start" })).toBeVisible()

    const later = page.locator("#pdf-page-container-1 .textLayer span").nth(2)
    await expect(later).toBeVisible()
    await endHandle.dragTo(later)

    await expect.poll(async () => bar.locator("span").first().innerText()).not.toBe(startQuote)
    const after = await bar.locator("span").first().innerText()
    expect(after.length).toBeGreaterThan(startQuote.length - 8)
  })
})
