import { expect, test } from "@playwright/test"
import { mockSharedLibrary } from "./shared-library-fixture"

test("shared catalog remains usable on a phone", async ({ page }) => {
  await mockSharedLibrary(page)
  await page.goto("/dashboard/shared")
  await expect(page.getByRole("heading", { name: "Shared library", exact: true })).toBeVisible()
  await expect(page.getByLabel("Search shared papers")).toBeVisible()
  await expect(page.getByRole("button", { name: "Select Attention Is All You Need", exact: true })).toBeVisible()
  const columns = await page.locator(".shared-document-grid").evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length)
  expect(columns).toBe(2)

  await page.getByRole("button", { name: "Select Attention Is All You Need", exact: true }).tap()
  await expect(page.getByText("1 selected", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Preview Attention Is All You Need", exact: true }).tap()
  await expect(page.getByRole("dialog").getByLabel("Page 1 of 2")).toBeVisible()
})
