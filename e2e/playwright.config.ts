import { defineConfig, devices } from "@playwright/test"

const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:3003"

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /phone\.spec/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "phone",
      testMatch: /phone\.spec/,
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
      },
    },
  ],
})
