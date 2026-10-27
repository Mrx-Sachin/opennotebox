import { expect, type Page } from "@playwright/test"

export const E2E_USER = process.env.E2E_USER || "e2e.dummy"
export const E2E_PASSWORD = process.env.E2E_PASSWORD || "E2eDummy123!"
export const E2E_DOC = "e2e-sample.pdf"

export async function login(page: Page, user = E2E_USER, password = E2E_PASSWORD) {
  await page.goto("/login")
  await page.evaluate(() => {
    localStorage.removeItem("token")
    localStorage.removeItem("refresh_token")
  })
  await page.reload()
  await page.locator("#username").fill(user)
  await page.locator("#password").fill(password)
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL("**/dashboard")
  await expect(page.getByTitle(`Sign out ${user}`)).toBeVisible()
}

export async function openLibrary(page: Page) {
  const lib = page.getByRole("button", { name: "Library" })
  if ((await lib.getAttribute("aria-pressed")) !== "true") {
    await lib.click()
  }
  await expect(page.getByRole("button", { name: /e2e-sample/ })).toBeVisible()
}

export async function openSampleDoc(page: Page) {
  await openLibrary(page)
  await page.getByRole("button", { name: /e2e-sample/ }).click()
  await expect(page.locator('input[type="number"][max="2"]')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator("#pdf-page-container-1 .textLayer span").first()).toBeVisible({
    timeout: 20_000,
  })
}

export async function openNotes(page: Page) {
  const notes = page.getByRole("button", { name: "Notes", exact: true })
  if ((await notes.getAttribute("aria-pressed")) !== "true") {
    await notes.click()
  }
  await expect(page.getByRole("button", { name: /Notes \(\d+\)/ })).toBeVisible()
}
