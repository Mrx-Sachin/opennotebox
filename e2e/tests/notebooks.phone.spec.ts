import { expect, test } from "@playwright/test"
import { mockLibrary } from "./library-fixture"

test("phone notebook shelf and document selection fit the screen", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  await page.goto("/dashboard")
  await expect(page.getByRole("link", { name: "Open notebook Research", exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole("link", { name: "Open notebook Research", exact: true }).click()
  await page.getByRole("checkbox", { name: "Select e2e-sample", exact: true }).check()
  await page.getByRole("button", { name: "Move to notebook", exact: true }).click()
  await expect(page.getByRole("dialog")).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByLabel("Destination").selectOption("")
  await page.getByRole("dialog").getByRole("button", { name: "Move documents", exact: true }).click()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(1)
})


test("phone picker cover selects a book and keeps the picker open", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  await page.goto("/dashboard?view=documents&move_to=1")
  await page.locator(".library-shelf .document-cover").nth(2).tap()
  await expect(page.getByRole("checkbox", { name: "Select Reading Notes 03", exact: true })).toBeChecked()
  await expect(page).toHaveURL(/move_to=1/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
