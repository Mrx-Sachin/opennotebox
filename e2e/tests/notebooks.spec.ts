import { expect, test } from "@playwright/test"
import { mockLibrary } from "./library-fixture"

test("homepage shows notebooks and a notebook opens only its sources", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  await page.goto("/dashboard")
  await expect(page.getByRole("heading", { name: "Your notebooks", exact: true })).toBeVisible()
  await expect(page.locator(".library-shelf")).toHaveCount(0)
  await page.getByRole("link", { name: "Open notebook Research", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Research", exact: true })).toBeVisible()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
  await page.reload()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
  await page.getByRole("button", { name: "Add documents", exact: true }).click()
  await expect(page.getByText("Adding to Research", { exact: true })).toBeVisible()
})

test("create a notebook, group existing documents, rename and unfile them by deleting it", async ({ page }) => {
  await mockLibrary(page)
  await page.goto("/dashboard")
  await page.getByRole("button", { name: "New notebook", exact: true }).click()
  await page.getByLabel("Notebook name", { exact: true }).fill("Machine learning")
  await page.getByLabel("Description (optional)").fill("Papers to read together")
  await page.getByRole("button", { name: "Create notebook", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Machine learning", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Add existing documents", exact: true }).first().click()
  await page.getByRole("checkbox", { name: "Select e2e-sample", exact: true }).check()
  await page.getByRole("checkbox", { name: "Select Research Methods", exact: true }).check()
  await page.getByRole("button", { name: "Add to Machine learning", exact: true }).click()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
  await page.getByRole("button", { name: "Edit notebook", exact: true }).click()
  await page.getByLabel("Notebook name", { exact: true }).fill("AI papers")
  await page.getByRole("button", { name: "Save notebook", exact: true }).click()
  await expect(page.getByRole("heading", { name: "AI papers", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Edit notebook", exact: true }).click()
  await page.getByRole("button", { name: "Delete notebook", exact: true }).click()
  await page.getByRole("button", { name: "Confirm delete notebook", exact: true }).click()
  await page.getByRole("link", { name: "Open Unfiled documents", exact: true }).click()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(24)
  await expect(page.getByRole("link", { name: "Open e2e-sample", exact: true }).last()).toBeVisible()
})


test("picker cover selects without navigation, source filter persists selection, and reader stays in destination", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  await page.goto("/dashboard")
  await page.getByRole("button", { name: "New notebook", exact: true }).click()
  await page.getByLabel("Notebook name", { exact: true }).fill("Notebook B")
  await page.getByRole("button", { name: "Create notebook", exact: true }).click()
  await page.getByRole("button", { name: "Add existing documents", exact: true }).first().click()
  await page.getByLabel("Source notebook").selectOption("1")
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
  await page.locator(".library-shelf .document-cover").first().click()
  await expect(page.getByRole("checkbox", { name: "Select e2e-sample", exact: true })).toBeChecked()
  await expect(page).toHaveURL(/move_to=/)
  await page.getByRole("checkbox", { name: "Select e2e-sample", exact: true }).focus()
  await page.keyboard.press("Space")
  await expect(page.getByRole("checkbox", { name: "Select e2e-sample", exact: true })).not.toBeChecked()
  await page.locator(".library-shelf .document-cover").first().click()
  await page.getByRole("button", { name: "Add to Notebook B", exact: true }).click()
  await page.locator(".library-shelf").getByRole("link", { name: "Open e2e-sample", exact: true }).click()
  await expect(page.getByRole("link", { name: "Back to notebook", exact: true })).toHaveAttribute("href", "/dashboard?notebook=2")
  await expect(page.locator("aside").getByTitle("Research Methods.epub", { exact: true })).toHaveCount(0)
  await page.reload()
  await page.getByRole("link", { name: "Back to notebook", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Notebook B", exact: true })).toBeVisible()
})


test("deleted and malformed notebook routes never fall through to all documents", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  for (const query of ["notebook=999", "notebook=invalid", "view=documents&move_to=999", "notebook=1&unfiled=true"]) {
    await page.goto(`/dashboard?${query}`)
    await expect(page.getByRole("heading", { name: "This notebook is unavailable", exact: true })).toBeVisible()
    await expect(page.locator(".library-document")).toHaveCount(0)
  }
})


test("picker excludes documents already filed and select-page retains other-page selections", async ({ page }) => {
  await mockLibrary(page, { notebooks: true })
  await page.goto("/dashboard?view=documents&move_to=1")
  await expect(page.getByRole("checkbox", { name: "Select e2e-sample", exact: true })).toBeDisabled()
  await page.getByRole("button", { name: "Select this page", exact: true }).click()
  await expect(page.getByText("22 selected", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Next page", exact: true }).click()
  await page.getByRole("button", { name: "Select this page", exact: true }).click()
  await expect(page.getByText("27 selected", { exact: true })).toBeVisible()
})

test("edit the name and description of Unfiled collection", async ({ page }) => {
  await mockLibrary(page)
  await page.goto("/dashboard")
  await expect(page.getByRole("link", { name: "Open Unfiled documents", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Unfiled", exact: true })).toBeVisible()

  // Click edit button on the Unfiled card
  await page.getByRole("button", { name: "Edit Unfiled", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Edit Unfiled", exact: true })).toBeVisible()
  await expect(page.getByText("Personalize the default collection for documents waiting for a notebook.")).toBeVisible()

  // Fill in new name and description
  await page.getByLabel("Collection name", { exact: true }).fill("Inbox")
  await page.getByLabel("Description (optional)").fill("Papers waiting to be sorted")
  await page.getByRole("button", { name: "Save changes", exact: true }).click()

  // Verify updated on the card
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible()
  await expect(page.getByText("Papers waiting to be sorted")).toBeVisible()
  await expect(page.getByRole("link", { name: "Open Inbox documents", exact: true })).toBeVisible()

  // Navigate into Inbox
  await page.getByRole("link", { name: "Open Inbox documents", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Edit Inbox", exact: true })).toBeVisible()

  // Edit from the header inside Inbox
  await page.getByRole("button", { name: "Edit Inbox", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Edit Inbox", exact: true })).toBeVisible()
  await page.getByLabel("Collection name", { exact: true }).fill("Read Later")
  await page.getByRole("button", { name: "Save changes", exact: true }).click()

  await expect(page.getByRole("heading", { name: "Read Later", exact: true })).toBeVisible()
})
