import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { expect, test } from "@playwright/test"

test("live sharing, authenticated preview and unsharing with the reusable QA account", async ({ page, request }) => {
  test.skip(process.env.E2E_LIVE_QA !== "1", "Opt-in check for the isolated preview")
  const accountPath = process.env.E2E_QA_ACCOUNT_FILE || join(homedir(), ".local/state/deepread/preview/testing/account.json")
  const account = JSON.parse(readFileSync(accountPath, "utf8"))
  const login = await request.post("/api/auth/login", { data: account })
  expect(login.ok()).toBe(true)
  const auth = await login.json()
  const headers = { Authorization: `Bearer ${auth.access_token}` }
  const documents = await (await request.get("/api/documents", { headers })).json()
  const document = documents.find((item: { title: string }) => item.title === "QA Control.pdf")
  expect(document).toBeTruthy()
  expect((await request.patch(`/api/shared-library/${document.id}`, { headers, data: { is_shared: false } })).ok()).toBe(true)
  await page.addInitScript((token) => localStorage.setItem("token", token), auth.access_token)
  try {
    await page.goto(`/dashboard/read/${document.id}`)
    await page.getByRole("button", { name: "Share with team", exact: true }).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByLabel("Title", { exact: true }).fill("QA Shared Reading Guide")
    await dialog.getByLabel("Topics", { exact: false }).fill("qa, reading")
    await dialog.getByLabel("Description", { exact: false }).fill("A small fixture for checking the team collection.")
    await dialog.getByRole("button", { name: "Share with team", exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await page.goto("/dashboard/shared")
    await page.getByRole("searchbox", { name: "Search shared papers" }).fill("QA Shared Reading Guide")
    const card = page.locator(".shared-document-card").filter({ hasText: "QA Shared Reading Guide" })
    await expect(card).toHaveCount(1)
    await expect(card.getByRole("button", { name: "QA Shared Reading Guide, already in your library" })).toBeDisabled()
    await card.getByRole("button", { name: "Preview QA Shared Reading Guide", exact: true }).click()
    await expect(page.getByRole("dialog").locator("canvas")).toBeVisible()
    await expect(page).toHaveURL(/\/dashboard\/shared$/)
    await page.keyboard.press("Escape")
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await page.goto(`/dashboard/read/${document.id}`)
    await page.getByRole("button", { name: "Manage sharing", exact: true }).click()
    await page.getByRole("dialog").getByRole("button", { name: "Stop sharing", exact: true }).click()
    await expect(page.getByRole("dialog")).toHaveCount(0)
    const catalog = await (await request.get("/api/shared-library?search=QA%20Shared%20Reading%20Guide", { headers })).json()
    expect(catalog.documents.some((item: { id: number }) => item.id === document.id)).toBe(false)
    expect((await request.get(`/api/documents/${document.id}/file`, { headers })).ok()).toBe(true)
  } finally {
    await request.patch(`/api/shared-library/${document.id}`, { headers, data: { is_shared: false } })
  }
})
