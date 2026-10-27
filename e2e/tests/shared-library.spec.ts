import { expect, test } from "@playwright/test"
import { mockSharedLibrary } from "./shared-library-fixture"

test("shared catalog searches, filters, paginates and previews one authenticated PDF page", async ({ page }) => {
  await mockSharedLibrary(page)
  await page.goto("/dashboard/shared")
  await expect(page.getByRole("heading", { name: "Shared library", exact: true })).toBeVisible()
  await expect(page.locator(".shared-document-card")).toHaveCount(24)
  await page.getByRole("button", { name: "Next page", exact: true }).click()
  await expect(page.locator(".shared-document-card")).toHaveCount(2)
  await expect(page.getByText("Page 2 of 2")).toBeVisible()
  await page.getByRole("button", { name: "Previous page", exact: true }).click()

  await page.getByLabel("Search shared papers").fill("Attention Is All You Need")
  await expect(page.locator(".shared-document-card")).toHaveCount(1)
  await page.getByRole("button", { name: "Preview Attention Is All You Need", exact: true }).click()
  const preview = page.getByRole("dialog").getByLabel("Page 1 of 2")
  await expect(preview).toBeVisible()
  await expect(page.getByRole("button", { name: "Next page", exact: true }).last()).toBeEnabled()
  await expect(page).not.toHaveURL(/\/dashboard\/read\//)
  await page.keyboard.press("Escape")

  await page.getByRole("button", { name: "Clear filters" }).click()
  await page.getByLabel("Topic").selectOption("machine learning")
  await expect(page.locator(".shared-document-card")).toHaveCount(13)
  await page.getByLabel("Document type").selectOption("textbook")
  await expect(page.locator(".shared-document-card")).toHaveCount(6)
})

test("catalog selection adds to a chosen notebook and disables additions already in the library", async ({ page }) => {
  const fixture = await mockSharedLibrary(page)
  await page.goto("/dashboard/shared?notebook=1")
  await expect(page.getByText("Suggestions for", { exact: true })).toBeVisible()
  await expect(page.getByText("Related to Research", { exact: true })).toBeVisible()

  await page.getByRole("button", { name: "Select Attention Is All You Need", exact: true }).click()
  await expect(page.getByText("1 selected", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Add to my notebook", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Add to my notebook", exact: true })).toBeVisible()
  await expect(page.getByLabel("Choose a destination")).toHaveValue("1")
  await expect(page.getByRole("option", { name: "Unfiled" })).toBeAttached()
  await page.getByLabel("Choose a destination").selectOption("")
  await page.getByRole("button", { name: "Add to my notebook", exact: true }).last().click()

  await expect(page.getByRole("status")).toContainText("1 paper added")
  expect(fixture.additions).toEqual([{ document_ids: [1], notebook_id: null }])
  await expect(page.getByRole("button", { name: "Attention Is All You Need, already in your library", exact: true })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Research Methods, already in your library", exact: true })).toBeDisabled()
  await expect(page).not.toHaveURL(/\/dashboard\/read\//)
})

test("private PDF owner can publish and then unshare its team listing", async ({ page }) => {
  const fixture = await mockSharedLibrary(page)
  await page.goto("/dashboard/read/1")
  await expect(page.getByRole("button", { name: "Share with team", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Share with team", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Share with team", exact: true })).toBeVisible()
  await expect(page.getByText("Your private notes and chats are never shared.")).toBeVisible()
  await page.getByLabel("Title", { exact: true }).fill("A private paper for the team")
  await page.getByLabel("Author (optional)", { exact: true }).fill("OpenNoteBox Reader")
  await page.getByLabel("Topics (comma separated)").fill("research, systems")
  await page.getByLabel("Description (optional)").fill("A careful guide to systems research.")
  await page.getByLabel("Document type", { exact: true }).selectOption("paper")
  await page.getByLabel("Language (optional)").fill("English")
  await page.getByRole("button", { name: "Share with team", exact: true }).last().click()

  await expect(page.getByRole("button", { name: "Manage sharing", exact: true })).toBeVisible()
  await expect(page.getByText("Listed in the team library", { exact: true })).toBeVisible()
  expect(fixture.updates[0]).toEqual({
    documentId: 1,
    body: {
      is_shared: true,
      title: "A private paper for the team",
      author: "OpenNoteBox Reader",
      description: "A careful guide to systems research.",
      topics: ["research", "systems"],
      document_type: "paper",
      language: "English",
    },
  })

  await page.getByRole("button", { name: "Manage sharing", exact: true }).click()
  await page.getByRole("button", { name: "Stop sharing", exact: true }).click()
  await expect(page.getByRole("button", { name: "Share with team", exact: true })).toBeVisible()
  expect(fixture.updates[1]).toEqual({ documentId: 1, body: { is_shared: false } })
})

test("an imported shared PDF stays read-only and cannot be republished", async ({ page }) => {
  await mockSharedLibrary(page, { readOnly: true, canShare: false })
  await page.goto("/dashboard/read/1")
  await expect(page.getByRole("button", { name: "Share with team", exact: true })).toHaveCount(0)
  await expect(page.locator("select[title='This shared document cannot be re-parsed']")).toBeDisabled()
  await expect(page.locator("button[title='This shared document cannot be re-parsed']")).toBeDisabled()
})
