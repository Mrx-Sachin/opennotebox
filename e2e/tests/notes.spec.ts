import { test, expect } from "@playwright/test"
import { login, openNotes, openSampleDoc } from "./helpers"

test.describe("notes", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await openSampleDoc(page)
    await openNotes(page)
  })

  test("cards clip long quotes instead of dumping the full passage", async ({ page }) => {
    await expect(page.getByText("e2e planted long highlight")).toBeVisible()
    const longCard = page.locator(".rounded-xl").filter({ hasText: "e2e planted long highlight" })
    await expect(longCard.getByText(/words/)).toBeVisible()
    const preview = await longCard.locator("p").first().innerText()
    expect(preview.length).toBeLessThan(200)
    expect(preview).not.toContain("available today")
  })

  test("the long highlight paints more than the title span", async ({ page }) => {
    const counts = await page.evaluate(() => {
      const layer = document.querySelector("#pdf-page-container-1 .textLayer")
      const leaves = [...(layer?.querySelectorAll("span") || [])].filter(
        (s) =>
          !s.classList.contains("markedContent") &&
          s.childElementCount === 0 &&
          (s.textContent || "").trim()
      )
      const hl = leaves.filter((s) => s.classList.contains("note-hl"))
      return { leaf: leaves.length, hl: hl.length }
    })
    expect(counts.leaf).toBeGreaterThan(1)
    expect(counts.hl).toBeGreaterThan(1)
  })

  test("selecting text opens a clipped floating bar", async ({ page }) => {
    await selectSample(page)
    const bar = page.locator("div.fixed.z-40")
    await expect(bar).toBeVisible()
    await expect(bar.getByRole("button", { name: "Highlight" })).toBeVisible()
    const chip = await bar.locator("span").first().innerText()
    expect(chip.length).toBeLessThan(80)
    await expect(page.getByRole("button", { name: "Move selection end" })).toHaveCount(0)
  })

  test("clicking the page dismisses the floating bar", async ({ page }) => {
    await selectSample(page)
    const bar = page.locator("div.fixed.z-40")
    await expect(bar).toBeVisible()
    await page.locator("#pdf-page-container-1").click({ position: { x: 24, y: 24 } })
    await expect(bar).toHaveCount(0)
  })

  test("saving a highlight removes the floating bar", async ({ page }) => {
    await selectSample(page)
    const bar = page.locator("div.fixed.z-40")
    await expect(bar).toBeVisible()
    await bar.getByRole("button", { name: "Highlight" }).click()
    await page.getByRole("button", { name: /^amber$/i }).click()
    await expect(bar).toHaveCount(0)
    const card = page.locator(".rounded-xl").filter({ hasText: "E2E Sample Paper" }).last()
    await expect(card.getByText("Amber", { exact: false })).toBeVisible()
    await card.getByRole("button", { name: "Delete" }).click()
  })
})

async function selectSample(page: import("@playwright/test").Page) {
  const selected = await page.evaluate(() => {
    const layer = document.querySelector("#pdf-page-container-1 .textLayer")
    const spans = [...(layer?.querySelectorAll("span") || [])].filter(
      (s) =>
        !s.classList.contains("markedContent") &&
        s.childElementCount === 0 &&
        (s.textContent || "").trim()
    )
    if (spans.length < 2) return null
    const a = spans[0]
    const b = spans[Math.min(2, spans.length - 1)]
    const range = document.createRange()
    range.setStart(a.firstChild || a, 0)
    const endNode = b.firstChild || b
    range.setEnd(endNode, Math.min(8, (endNode.textContent || "").length))
    const sel = window.getSelection()
    sel?.removeAllRanges()
    sel?.addRange(range)
    layer?.dispatchEvent(
      new PointerEvent("pointerup", {
        bubbles: true,
        clientX: 400,
        clientY: 400,
        pointerId: 1,
        pointerType: "mouse",
        button: 0,
      })
    )
    return sel?.toString() || ""
  })
  expect(selected && selected.length).toBeGreaterThan(0)
}
