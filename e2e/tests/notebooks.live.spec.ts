import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { expect, test } from "@playwright/test"

test("live notebook filing, preview and reader context with the reusable QA account", async ({ page, request }) => {
  test.skip(process.env.E2E_LIVE_QA !== "1", "Opt-in check for the isolated preview")
  const accountPath = process.env.E2E_QA_ACCOUNT_FILE || join(homedir(), ".local/state/deepread/preview/testing/account.json")
  const account = JSON.parse(readFileSync(accountPath, "utf8"))
  const login = await request.post("/api/auth/login", { data: account })
  expect(login.ok()).toBe(true)
  const auth = await login.json()
  const headers = { Authorization: `Bearer ${auth.access_token}` }
  let notebooks = await (await request.get("/api/notebooks", { headers })).json()
  for (const name of ["QA Notebook A", "QA Notebook B"]) {
    if (!notebooks.some((item: { name: string }) => item.name === name)) {
      expect((await request.post("/api/notebooks", { headers, data: { name } })).ok()).toBe(true)
    }
  }
  notebooks = await (await request.get("/api/notebooks", { headers })).json()
  const a = notebooks.find((item: { name: string }) => item.name === "QA Notebook A")
  const b = notebooks.find((item: { name: string }) => item.name === "QA Notebook B")
  const documents = await (await request.get("/api/documents", { headers })).json()
  const fixtures = documents.filter((item: { title: string }) => ["QA Notebook Guide.pdf", "QA Research Notes.pdf", "QA Control.pdf"].includes(item.title))
  expect(fixtures).toHaveLength(3)
  expect((await request.post("/api/notebooks/move-documents", { headers, data: { document_ids: fixtures.map((item: { id: number }) => item.id), notebook_id: a.id } })).ok()).toBe(true)
  await page.addInitScript((token) => localStorage.setItem("token", token), auth.access_token)
  await page.goto(`/dashboard?notebook=${b.id}`)
  await page.getByRole("button", { name: "Add existing documents", exact: true }).first().click()
  await page.getByLabel("Source notebook").selectOption(String(a.id))
  const card = page.locator(".library-shelf .library-document-entry").filter({ has: page.getByRole("heading", { name: "QA Notebook Guide", exact: true }) })
  await card.locator(".document-cover").click()
  await expect(page.getByRole("checkbox", { name: "Select QA Notebook Guide", exact: true })).toBeChecked()
  await expect(page).toHaveURL(/move_to=/)
  const popupPromise = page.waitForEvent("popup")
  await card.getByRole("link", { name: "Preview QA Notebook Guide in a new tab", exact: true }).click()
  const popup = await popupPromise
  await popup.waitForURL(/\/dashboard\/read\//)
  await popup.close()
  await expect(page.getByRole("checkbox", { name: "Select QA Notebook Guide", exact: true })).toBeChecked()
  await page.getByRole("checkbox", { name: "Select QA Research Notes", exact: true }).check()
  await page.getByRole("button", { name: "Add to QA Notebook B", exact: true }).click()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
  await page.locator(".library-shelf").getByRole("link", { name: "Open QA Notebook Guide", exact: true }).click()
  await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
  await expect(page.locator("aside").getByTitle("QA Control.pdf", { exact: true })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole("link", { name: "Back to notebook", exact: true })).toHaveAttribute("href", `/dashboard?notebook=${b.id}`)
  await page.getByRole("link", { name: "Back to notebook", exact: true }).click()
  await expect(page.getByRole("heading", { name: "QA Notebook B", exact: true })).toBeVisible()
  await expect(page.locator(".library-shelf .library-document")).toHaveCount(2)
})
