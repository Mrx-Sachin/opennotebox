import { expect, test } from "@playwright/test"
import { mockLibrary } from "./library-fixture"

test.describe("Settings modal voice catalog", () => {
  test("shows only six Kokoro voices and saves voice selection", async ({ page }) => {
    let currentSettings = {
      llm: { active: "omniroute", backends: [] },
      tts: { default_voice: "kokoro-af_heart" },
      export: { enabled: false },
    }

    const kokoroVoices = [
      {
        id: "kokoro-af_heart",
        label: "Heart (female, US, Kokoro)",
        description: "Top-graded Kokoro voice. Natural, expressive.",
      },
      {
        id: "kokoro-af_bella",
        label: "Bella (female, US, Kokoro)",
        description: "Natural, expressive.",
      },
      {
        id: "kokoro-am_michael",
        label: "Michael (male, US, Kokoro)",
        description: "Natural, expressive.",
      },
      {
        id: "kokoro-am_fenrir",
        label: "Fenrir (male, US, Kokoro)",
        description: "Natural, expressive.",
      },
      {
        id: "kokoro-bf_emma",
        label: "Emma (female, British, Kokoro)",
        description: "Natural, expressive.",
      },
      {
        id: "kokoro-bm_george",
        label: "George (male, British, Kokoro)",
        description: "Natural, expressive.",
      },
    ]

    await mockLibrary(page)

    let updatedVoice: string | null = null

    await page.route("**/api/settings", async (route) => {
      const request = route.request()
      if (request.method() === "GET") {
        return route.fulfill({ json: currentSettings })
      }
      if (request.method() === "PUT") {
        const data = request.postDataJSON()
        if (data.tts?.default_voice) {
          updatedVoice = data.tts.default_voice
          currentSettings = {
            ...currentSettings,
            tts: { default_voice: data.tts.default_voice },
          }
        }
        return route.fulfill({ json: currentSettings })
      }
      return route.fallback()
    })

    await page.route("**/api/tts/voices", async (route) => {
      return route.fulfill({ json: { voices: kokoroVoices } })
    })

    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Your library", exact: true })).toBeVisible()

    await page.locator('button[title="Settings"]').click()
    await expect(page.getByRole("button", { name: "Voice" })).toBeVisible()

    await page.getByRole("button", { name: "Voice" }).click()
    await expect(page.getByRole("heading", { name: "Voice" })).toBeVisible()

    for (const voice of kokoroVoices) {
      await expect(page.getByText(voice.label)).toBeVisible()
    }

    await expect(page.getByText("Narrator (libritts_r)")).not.toBeVisible()
    await expect(page.getByText("Host A - Ryan")).not.toBeVisible()
    await expect(page.getByText("Host B - Lessac")).not.toBeVisible()
    await expect(page.getByText(/slower than piper/i)).not.toBeVisible()

    await page.getByText("Bella (female, US, Kokoro)").click()
    await expect.poll(() => updatedVoice).toBe("kokoro-af_bella")
  })
})
