import { expect, test } from "@playwright/test"
import { mockLibrary } from "./library-fixture"

test("phone gallery fits the screen and supports filters and uploads", async ({ page }) => {
  await mockLibrary(page)
  await page.goto("/dashboard?view=documents")
  await expect(page.getByRole("heading", { name: "Your library", exact: true })).toBeVisible()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(24)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByLabel("Search documents").fill("Research")
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(1)
  await page.getByRole("button", { name: "Add documents", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Add sources", exact: true })).toBeVisible()
})
