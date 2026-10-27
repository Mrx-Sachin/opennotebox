import { test, expect } from "@playwright/test"
import { E2E_PASSWORD, E2E_USER, login } from "./helpers"

test.describe("auth", () => {
  test("rejects a bad password", async ({ page }) => {
    await page.goto("/login")
    await page.evaluate(() => {
      localStorage.removeItem("token")
      localStorage.removeItem("refresh_token")
    })
    await page.reload()
    await page.locator("#username").fill(E2E_USER)
    await page.locator("#password").fill("wrong-password")
    await page.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByText(/incorrect username or password/i)).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })

  test("dummy account can sign in and sign out", async ({ page }) => {
    await login(page)
    await expect(page.getByText(E2E_USER)).toBeVisible()
    await page.getByTitle(`Sign out ${E2E_USER}`).click()
    await page.waitForURL("**/login")
    await expect(page.locator("#username")).toBeVisible()
  })
})
