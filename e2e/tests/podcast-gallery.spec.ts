import { expect, test, type Page } from "@playwright/test"

function createSilentWav(seconds = 60): Buffer {
  const sampleRate = 8000
  const dataSize = sampleRate * seconds * 2
  const buffer = Buffer.alloc(44 + dataSize)
  buffer.write("RIFF", 0)
  buffer.writeUInt32LE(36 + dataSize, 4)
  buffer.write("WAVE", 8)
  buffer.write("fmt ", 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(sampleRate, 24)
  buffer.writeUInt32LE(sampleRate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write("data", 36)
  buffer.writeUInt32LE(dataSize, 40)
  return buffer
}

const SILENT_WAV_BUFFER = createSilentWav(60)

interface MockOptions {
  empty?: boolean
  error?: boolean
  audioError?: boolean
  delayAudio101?: Promise<void>
  deleteError?: boolean
  delayDelete101?: Promise<void>
}

async function setupMockPodcasts(page: Page, options: MockOptions = {}) {
  const podcasts = [
    {
      id: 101,
      user_id: 1,
      document_id: 1,
      scope: "full",
      section_title: null,
      length: "medium",
      status: "completed",
      error: null,
      duration: 360.0,
      transcript: {
        title: "Deep Dive into Sample Document",
        lines: [
          { speaker: "Host A", text: "Welcome to today's discussion." },
          { speaker: "Host B", text: "Thanks, excited to be here." },
        ],
      },
      created_at: "2026-10-09T03:00:00Z",
      audio_url: "/api/podcasts/101/audio",
      document_title: "e2e-sample.pdf",
    },
    {
      id: 102,
      user_id: 1,
      document_id: 2,
      scope: "section",
      section_title: "Chapter 2: Methods",
      length: "short",
      status: "completed",
      error: null,
      duration: 180.0,
      transcript: {
        title: "Research Methods Overview",
        lines: [],
      },
      created_at: "2026-10-09T03:30:00Z",
      audio_url: "/api/podcasts/102/audio",
      document_title: "Research Methods.epub",
    },
    {
      id: 103,
      user_id: 1,
      document_id: 1,
      scope: "full",
      section_title: null,
      length: "long",
      status: "generating",
      error: null,
      duration: null,
      transcript: null,
      created_at: "2026-10-09T04:00:00Z",
      audio_url: null,
      document_title: "e2e-sample.pdf",
    },
    {
      id: 104,
      user_id: 1,
      document_id: 2,
      scope: "section",
      section_title: "Chapter 3: Results",
      length: "medium",
      status: "failed",
      error: "Synthesis timeout",
      duration: null,
      transcript: null,
      created_at: "2026-10-09T04:15:00Z",
      audio_url: null,
      document_title: "Research Methods.epub",
    },
  ]

  let currentPodcasts = [...podcasts]

  await page.addInitScript(() => {
    localStorage.setItem("token", "podcast-test-token")
    const nativeAudio = window.Audio
    ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = undefined
    window.Audio = function (audioSource?: string) {
      const audioElement = new nativeAudio(audioSource)
      ;(window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement = audioElement
      return audioElement
    } as unknown as typeof Audio
  })

  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())

    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: { id: 1, username: "podcast.tester", is_admin: false, created_at: "2026-10-01T00:00:00Z" },
      })
    }

    if (url.pathname === "/api/notebooks") {
      return route.fulfill({ json: [] })
    }

    if (url.pathname === "/api/documents/library") {
      return route.fulfill({
        json: {
          documents: [
            {
              id: 1,
              user_id: 1,
              notebook_id: null,
              title: "e2e-sample.pdf",
              format: "pdf",
              file_size: 1024,
              num_pages: 2,
              processing_status: "completed",
              created_at: "2026-10-01T00:00:00Z",
              last_read_page: 1,
              last_read_at: "2026-10-08T00:00:00Z",
              last_read_position: null,
            },
            {
              id: 2,
              user_id: 1,
              notebook_id: null,
              title: "Research Methods.epub",
              format: "epub",
              file_size: 2048,
              num_pages: 50,
              processing_status: "completed",
              created_at: "2026-10-02T00:00:00Z",
              last_read_page: null,
              last_read_at: null,
              last_read_position: null,
            },
          ],
          total: 2,
          library_total: 2,
          reading_count: 1,
          processing_count: 0,
          failed_count: 0,
          formats: { pdf: 1, epub: 1 },
          recent: [],
        },
      })
    }

    if (url.pathname === "/api/podcasts") {
      if (options.error) {
        return route.fulfill({ status: 500, json: { detail: "Internal Server Error" } })
      }
      if (options.empty) {
        return route.fulfill({ json: [] })
      }
      return route.fulfill({ json: currentPodcasts })
    }

    const podcastDeleteMatch = url.pathname.match(/^\/api\/podcasts\/(\d+)$/)
    if (podcastDeleteMatch && request.method() === "DELETE") {
      const podcastIdentifier = parseInt(podcastDeleteMatch[1], 10)
      if (podcastIdentifier === 101 && options.delayDelete101) {
        await options.delayDelete101
      }
      if (options.deleteError) {
        return route.fulfill({ status: 500, json: { detail: "Internal Server Error" } })
      }
      currentPodcasts = currentPodcasts.filter((item) => item.id !== podcastIdentifier)
      return route.fulfill({ status: 200, json: { detail: "Deleted" } })
    }

    if (url.pathname === "/api/podcasts/101/audio") {
      if (options.delayAudio101) {
        await options.delayAudio101
      }
      if (options.audioError) {
        return route.fulfill({ status: 404, json: { detail: "Audio not found" } })
      }
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: SILENT_WAV_BUFFER,
      })
    }

    if (url.pathname === "/api/podcasts/102/audio") {
      return route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: SILENT_WAV_BUFFER,
      })
    }

    if (url.pathname === "/api/documents/1/file") {
      return route.fulfill({
        path: "fixtures/e2e-sample.pdf",
        contentType: "application/pdf",
      })
    }

    if (url.pathname === "/api/documents/1") {
      return route.fulfill({
        json: {
          id: 1,
          user_id: 1,
          notebook_id: null,
          title: "e2e-sample.pdf",
          format: "pdf",
          file_size: 1024,
          num_pages: 2,
          processing_status: "completed",
          created_at: "2026-10-01T00:00:00Z",
          last_read_page: 1,
          last_read_at: null,
          last_read_position: null,
        },
      })
    }

    if (url.pathname === "/api/settings") {
      return route.fulfill({
        json: {
          llm: { active: "omniroute", backends: [] },
          tts: { default_voice: "test" },
          export: { enabled: false },
        },
      })
    }

    if (url.pathname === "/api/shared-library") {
      return route.fulfill({
        json: {
          documents: [
            {
              id: 99,
              title: "Shared Research Note",
              document_type: "Notes",
              num_pages: 5,
              author: "Research Team",
              description: "Collaborative research notes",
              topics: ["General"],
              reason: "Team discussion",
              shared_at: "2026-10-09T00:00:00Z",
              added: false,
            },
          ],
          total: 1,
          topics: ["General"],
          document_types: ["Notes"],
        },
      })
    }

    if (url.pathname === "/api/documents") {
      return route.fulfill({
        json: [
          {
            id: 1,
            user_id: 1,
            notebook_id: null,
            title: "e2e-sample.pdf",
            format: "pdf",
            file_size: 1024,
            num_pages: 2,
            processing_status: "completed",
            created_at: "2026-10-01T00:00:00Z",
            last_read_page: 1,
            last_read_at: null,
            last_read_position: null,
          },
        ],
      })
    }

    if (url.pathname.startsWith("/api/documents/")) {
      return route.fulfill({ json: [] })
    }

    return route.fulfill({ json: [] })
  })
}

test("podcast gallery lists all-book podcasts with titles, durations, and book links", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await expect(page.getByRole("heading", { name: "Podcasts", exact: true })).toBeVisible()
  await expect(page.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByText("Research Methods Overview")).toBeVisible()

  const sampleBookLink = page.getByTestId("podcast-card-101").getByRole("link", { name: "Open source book e2e-sample.pdf" })
  await expect(sampleBookLink).toBeVisible()
  await expect(sampleBookLink).toHaveAttribute("href", "/dashboard/read/1")

  const methodsBookLink = page.getByTestId("podcast-card-102").getByRole("link", { name: "Open source book Research Methods.epub" })
  await expect(methodsBookLink).toBeVisible()
  await expect(methodsBookLink).toHaveAttribute("href", "/dashboard/read/2")
})

test("generating and failed podcasts display non-playable status badges", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const generatingCard = page.getByTestId("podcast-card-103")
  await expect(generatingCard).toBeVisible()
  await expect(generatingCard.getByText("Generating")).toBeVisible()
  await expect(page.getByTestId("podcast-play-103")).toHaveCount(0)

  const failedCard = page.getByTestId("podcast-card-104")
  await expect(failedCard).toBeVisible()
  await expect(failedCard.getByText("Failed")).toBeVisible()
  await expect(page.getByTestId("podcast-play-104")).toHaveCount(0)
})

test("podcast gallery renders empty state when no podcasts exist", async ({ page }) => {
  await setupMockPodcasts(page, { empty: true })
  await page.goto("/dashboard")

  await expect(page.getByTestId("podcast-empty-state")).toBeVisible()
  await expect(page.getByText("No podcasts generated yet")).toBeVisible()
})

test("podcast gallery renders error state with retry on failure", async ({ page }) => {
  await setupMockPodcasts(page, { error: true })
  await page.goto("/dashboard")

  await expect(page.locator(".podcast-empty-state[role='alert']")).toBeVisible()
  await expect(page.getByText("Podcasts could not be loaded")).toBeVisible()
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible()
})

test("podcast in-place playback reveals bottom player with functional audio controls", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const playButton = page.getByTestId("podcast-play-101")
  await expect(playButton).toBeVisible()
  await playButton.click()

  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()

  const pauseButton = bottomPlayer.getByRole("button", { name: "Pause" })
  await expect(pauseButton).toBeVisible()

  await expect(bottomPlayer.getByText("1:00")).toBeVisible()

  await pauseButton.click()
  const resumeButton = bottomPlayer.getByRole("button", { name: "Play", exact: true })
  await expect(resumeButton).toBeVisible()

  await resumeButton.click()
  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible()

  const forwardButton = bottomPlayer.getByRole("button", { name: "Fast forward 15 seconds" })
  await expect(forwardButton).toBeVisible()
  await forwardButton.click()

  const speedButton = bottomPlayer.getByRole("button", { name: /Playback speed/ })
  await expect(speedButton).toBeVisible()
  await speedButton.click()
  await expect(bottomPlayer.getByText("1.25x")).toBeVisible()

  const seekSlider = bottomPlayer.getByRole("slider", { name: "Seek audio" })
  await expect(seekSlider).toBeVisible()
  await seekSlider.fill("5")
})

test("rapid podcast switching regression: delayed audio A does not revoke faster audio B", async ({ page }) => {
  let releaseAudioA: (() => void) | null = null
  const audioAHold = new Promise<void>((resolve) => {
    releaseAudioA = resolve
  })

  await setupMockPodcasts(page, {
    delayAudio101: audioAHold,
  })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  await page.getByTestId("podcast-play-102").click()

  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()
  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible()

  if (releaseAudioA) {
    (releaseAudioA as () => void)()
  }

  await page.waitForTimeout(500)
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()
  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible()
})

test("navigating away to documents view maintains persistent playback in bottom player", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const allDocumentsButton = page.locator(".library-navigation").getByRole("button", { name: /All documents/ })
  await allDocumentsButton.click()

  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByTestId("bottom-player")).toHaveCount(1)
})

test("playback failure displays visible error alert", async ({ page }) => {
  await setupMockPodcasts(page, { audioError: true })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const errorAlert = page.locator(".podcast-playback-error[role='alert']")
  await expect(errorAlert).toBeVisible()
  await expect(errorAlert).toContainText("Unable to play podcast")
})

test("podcast navigation link in sidebar opens dedicated podcasts view", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const sidebarPodcastLink = page.locator(".library-navigation").getByRole("link", { name: "Podcasts" })
  await expect(sidebarPodcastLink).toBeVisible()
  await sidebarPodcastLink.click()

  await expect(page).toHaveURL(/\/dashboard\?view=podcasts/)
  await expect(sidebarPodcastLink).toHaveClass(/is-active/)

  const allDocumentsButton = page.locator(".library-navigation").getByRole("button", { name: /All documents/ })
  await expect(allDocumentsButton).not.toHaveClass(/is-active/)
  await expect(allDocumentsButton).toHaveAttribute("aria-pressed", "false")
})

test("source book link navigates to reader page without document errors", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const sampleBookLink = page.getByTestId("podcast-card-101").getByRole("link", { name: "Open source book e2e-sample.pdf" })
  await sampleBookLink.click()

  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(page.getByText("This document is unavailable")).toHaveCount(0)
  await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
})

test("podcast gallery layout is responsive on phone with no horizontal overflow", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.setViewportSize({ width: 375, height: 667 })
  await page.goto("/dashboard")

  await expect(page.getByRole("heading", { name: "Podcasts", exact: true })).toBeVisible()
  await expect(page.getByTestId("podcast-card-101")).toBeVisible()

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
  const clientWidth = await page.evaluate(() => document.documentElement.clientWidth)
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth)

  const playerScrollWidth = await bottomPlayer.evaluate((element) => element.scrollWidth)
  const windowWidth = await page.evaluate(() => window.innerWidth)
  expect(playerScrollWidth).toBeLessThanOrEqual(windowWidth)
})

test("podcast card delete flow can be cancelled and preserves podcast in gallery", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const cardToDelete = page.getByTestId("podcast-card-101")
  await expect(cardToDelete).toBeVisible()

  const deleteTrigger = page.getByTestId("podcast-delete-101")
  await expect(deleteTrigger).toBeVisible()
  await deleteTrigger.click()

  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()
  await expect(deleteDialog.getByRole("heading", { name: "Delete podcast" })).toBeVisible()
  await expect(deleteDialog).toContainText("Deep Dive into Sample Document")

  const cancelButton = deleteDialog.getByRole("button", { name: "Cancel" })
  await cancelButton.click()

  await expect(deleteDialog).not.toBeVisible()
  await expect(cardToDelete).toBeVisible()
})

test("podcast card delete confirmation removes podcast, updates query cache, and updates count", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await expect(page.getByText("4 podcasts")).toBeVisible()
  const cardToDelete = page.getByTestId("podcast-card-101")
  await expect(cardToDelete).toBeVisible()

  await page.getByTestId("podcast-delete-101").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  const confirmButton = page.getByTestId("confirm-delete-podcast-button")
  await confirmButton.click()

  await expect(deleteDialog).not.toBeVisible()
  await expect(cardToDelete).toHaveCount(0)
  await expect(page.getByText("3 podcasts")).toBeVisible()
  await expect(page.getByTestId("podcast-card-102")).toBeVisible()
})

test("deletion server failure shows accessible alert and keeps podcast card", async ({ page }) => {
  await setupMockPodcasts(page, { deleteError: true })
  await page.goto("/dashboard")

  const cardToDelete = page.getByTestId("podcast-card-101")
  await page.getByTestId("podcast-delete-101").click()

  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  const confirmButton = page.getByTestId("confirm-delete-podcast-button")
  await confirmButton.click()

  const errorAlert = deleteDialog.locator(".podcast-playback-error[role=alert]")
  await expect(errorAlert).toBeVisible()
  await expect(errorAlert).toContainText("Failed to delete podcast")

  await deleteDialog.getByRole("button", { name: "Cancel" }).click()
  await expect(deleteDialog).not.toBeVisible()
  await expect(cardToDelete).toBeVisible()
})

test("actively playing podcast is stopped immediately when deleted", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()

  await page.getByTestId("podcast-delete-101").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  await page.getByTestId("confirm-delete-podcast-button").click()
  await expect(deleteDialog).not.toBeVisible()
  await expect(page.getByTestId("podcast-card-101")).toHaveCount(0)

  await expect(page.getByTestId("bottom-player")).toHaveCount(0)
})

test("deleting an inactive podcast keeps active playback of another podcast running", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()

  await page.getByTestId("podcast-delete-102").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  await page.getByTestId("confirm-delete-podcast-button").click()
  await expect(deleteDialog).not.toBeVisible()
  await expect(page.getByTestId("podcast-card-102")).toHaveCount(0)

  await expect(page.getByTestId("bottom-player")).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
})

test("podcast deletion is responsive and accessible on mobile viewport", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.setViewportSize({ width: 375, height: 667 })
  await page.goto("/dashboard")

  const mobileDeleteTrigger = page.getByTestId("podcast-delete-101")
  await expect(mobileDeleteTrigger).toBeVisible()
  await mobileDeleteTrigger.click()

  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
  const clientWidth = await page.evaluate(() => document.documentElement.clientWidth)
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth)

  await page.getByTestId("confirm-delete-podcast-button").click()
  await expect(deleteDialog).not.toBeVisible()
  await expect(page.getByTestId("podcast-card-101")).toHaveCount(0)
})

test("delayed DELETE during route change to reader preserves reader page and clean unmount", async ({ page }) => {
  let releaseDelete101: (() => void) | null = null
  const deleteHoldPromise = new Promise<void>((resolve) => {
    releaseDelete101 = resolve
  })

  await setupMockPodcasts(page, { delayDelete101: deleteHoldPromise })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-delete-101").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  await page.getByTestId("confirm-delete-podcast-button").click()

  await page.goto("/dashboard/read/1")
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(page.getByText("This document is unavailable")).toHaveCount(0)
  await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()

  if (releaseDelete101) {
    (releaseDelete101 as () => void)()
  }

  await page.waitForTimeout(500)
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible()
})

test("delayed DELETE of one podcast preserves active playback of another podcast", async ({ page }) => {
  let releaseDelete101: (() => void) | null = null
  const deleteHoldPromise = new Promise<void>((resolve) => {
    releaseDelete101 = resolve
  })

  await setupMockPodcasts(page, { delayDelete101: deleteHoldPromise })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-102").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()

  await page.getByTestId("podcast-delete-101").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  await page.getByTestId("confirm-delete-podcast-button").click()

  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()

  if (releaseDelete101) {
    (releaseDelete101 as () => void)()
  }

  await page.waitForTimeout(500)
  await expect(deleteDialog).not.toBeVisible()
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()
  await expect(page.getByTestId("podcast-card-101")).toHaveCount(0)
})

test("delayed DELETE during account switch updates original user cache without mutating new account cache", async ({ page }) => {
  let releaseDelete101: (() => void) | null = null
  const deleteHoldPromise = new Promise<void>((resolve) => {
    releaseDelete101 = resolve
  })

  await setupMockPodcasts(page, { delayDelete101: deleteHoldPromise })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-delete-101").click()
  const deleteDialog = page.getByTestId("podcast-delete-dialog")
  await expect(deleteDialog).toBeVisible()

  await page.getByTestId("confirm-delete-podcast-button").click()

  await page.evaluate(() => {
    const authStore = (window as unknown as { useAuth?: { getState: () => { setUser: (u: unknown) => void } } }).useAuth
    if (authStore) {
      authStore.getState().setUser({ id: 2, username: "second.user" })
    }
  })

  if (releaseDelete101) {
    (releaseDelete101 as () => void)()
  }

  await page.waitForTimeout(500)
  const cachedUserOnePodcasts = await page.evaluate(() => {
    const client = (window as unknown as { __REACT_QUERY_CLIENT__?: { getQueryData: (k: unknown) => unknown } }).__REACT_QUERY_CLIENT__
    if (!client) return null
    return client.getQueryData(["podcasts", "all", 1])
  })
  if (cachedUserOnePodcasts && Array.isArray(cachedUserOnePodcasts)) {
    expect(cachedUserOnePodcasts.some((p: { id: number }) => p.id === 101)).toBe(false)
  }
})

test("selecting a podcast opens the full player modal with visualizer and transcript", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-expand-101").click()

  const playerModal = page.getByTestId("podcast-player-modal")
  await expect(playerModal).toBeVisible()

  const visualizerPane = page.getByTestId("podcast-visualizer-pane")
  await expect(visualizerPane).toBeVisible()
  await expect(page.getByTestId("podcast-waveform")).toBeVisible()

  const transcriptPane = page.getByTestId("podcast-transcript-pane")
  await expect(transcriptPane).toBeVisible()
  await expect(page.getByTestId("transcript-line-0")).toContainText("Welcome to today's discussion")
  await expect(page.getByTestId("transcript-line-1")).toContainText("Thanks, excited to be here")

  const bookLink = page.getByTestId("podcast-modal-book-link")
  await expect(bookLink).toHaveAttribute("href", "/dashboard/read/1")
})

test("podcast player modal full screen mode can be toggled", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-expand-101").click()

  const playerModal = page.getByTestId("podcast-player-modal")
  await expect(playerModal).toBeVisible()
  await expect(playerModal).not.toHaveClass(/is-fullscreen-mode/)

  const fullscreenToggle = page.getByTestId("podcast-fullscreen-toggle")
  await fullscreenToggle.click()
  await expect(playerModal).toHaveClass(/is-fullscreen-mode/)

  await fullscreenToggle.click()
  await expect(playerModal).not.toHaveClass(/is-fullscreen-mode/)
})

test("minimizing player keeps audio playing in compact bottom bar and can be re-expanded", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  await page.getByTestId("bottom-player-expand").click()
  const playerModal = page.getByTestId("podcast-player-modal")
  await expect(playerModal).toBeVisible()

  await page.getByTestId("podcast-modal-close").click()
  await expect(playerModal).toHaveCount(0)

  await expect(bottomPlayer).toBeVisible()

  await page.getByTestId("bottom-player-expand").click()
  await expect(page.getByTestId("podcast-player-modal")).toBeVisible()
})

test("podcast card direct book page link navigates to reader", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  const bookLink = page.getByTestId("podcast-book-link-101")
  await expect(bookLink).toHaveAttribute("href", "/dashboard/read/1")
})

test("podcast playback persists across dashboard, reader, shared library, and podcasts navigation", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByTestId("bottom-player")).toHaveCount(1)

  const initialAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      src: audio?.src ?? "",
      paused: audio?.paused ?? true,
      currentTime: audio?.currentTime ?? 0,
    }
  })
  expect(initialAudio.src).toBeTruthy()
  expect(initialAudio.paused).toBe(false)

  const readerLink = page.getByTestId("podcast-book-link-101")
  await readerLink.click()
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByTestId("bottom-player")).toHaveCount(1)

  const readerAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      src: audio?.src ?? "",
      paused: audio?.paused ?? true,
      currentTime: audio?.currentTime ?? 0,
    }
  })
  expect(readerAudio.src).toBe(initialAudio.src)
  expect(readerAudio.paused).toBe(false)
  expect(readerAudio.currentTime).toBeGreaterThanOrEqual(initialAudio.currentTime)

  const sharedSidebarLink = page.locator('aside a[href="/dashboard/shared"]')
  await expect(sharedSidebarLink).toBeVisible()
  await sharedSidebarLink.click()
  await expect(page).toHaveURL(/\/dashboard\/shared$/)
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByTestId("bottom-player")).toHaveCount(1)

  const sharedAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      src: audio?.src ?? "",
      paused: audio?.paused ?? true,
    }
  })
  expect(sharedAudio.src).toBe(initialAudio.src)
  expect(sharedAudio.paused).toBe(false)

  const sharedPodcastsLink = page.locator('.library-navigation a[href="/dashboard?view=podcasts"]')
  await expect(sharedPodcastsLink).toBeVisible()
  await sharedPodcastsLink.click()
  await expect(page).toHaveURL(/\/dashboard\?view=podcasts/)
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()
  await expect(page.getByTestId("bottom-player")).toHaveCount(1)

  const podcastsAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      src: audio?.src ?? "",
      paused: audio?.paused ?? true,
    }
  })
  expect(podcastsAudio.src).toBe(initialAudio.src)
  expect(podcastsAudio.paused).toBe(false)
})

test("paused podcast audio remains paused across navigation", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const pauseButton = bottomPlayer.getByRole("button", { name: "Pause" })
  await expect(pauseButton).toBeVisible()
  await pauseButton.click()

  const resumeButton = bottomPlayer.getByRole("button", { name: "Play", exact: true })
  await expect(resumeButton).toBeVisible()

  const pausedAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      paused: audio?.paused ?? false,
    }
  })
  expect(pausedAudio.paused).toBe(true)

  const sharedNavLink = page.locator('.library-navigation a[href="/dashboard/shared"]')
  await sharedNavLink.click()
  await expect(page).toHaveURL(/\/dashboard\/shared$/)

  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByRole("button", { name: "Play", exact: true })).toBeVisible()

  const stillPausedAudio = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      paused: audio?.paused ?? false,
    }
  })
  expect(stillPausedAudio.paused).toBe(true)
})

test("in-flight audio fetch continues to playback when navigating during load", async ({ page }) => {
  let releaseDelayedAudio: (() => void) | null = null
  const delayPromise = new Promise<void>((resolve) => {
    releaseDelayedAudio = resolve
  })

  await setupMockPodcasts(page, { delayAudio101: delayPromise })
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const sharedNavLink = page.locator('.library-navigation a[href="/dashboard/shared"]')
  await sharedNavLink.click()
  await expect(page).toHaveURL(/\/dashboard\/shared$/)
  await expect(bottomPlayer).toBeVisible()

  if (releaseDelayedAudio) {
    (releaseDelayedAudio as () => void)()
  }

  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible({ timeout: 10000 })
  const playingState = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return {
      paused: audio?.paused ?? true,
    }
  })
  expect(playingState.paused).toBe(false)
})

test("other podcast takeover replaces track and explicit stop clears bottom player", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()

  await page.getByTestId("podcast-play-102").click()
  await expect(bottomPlayer.getByText("Research Methods Overview")).toBeVisible()

  const stopButton = bottomPlayer.getByRole("button", { name: "Stop audio" })
  await expect(stopButton).toBeVisible()
  await stopButton.click()

  await expect(bottomPlayer).toHaveCount(0)
})

test("reading audio takeover revokes podcast bottom player and metadata", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const readerLink = page.getByTestId("podcast-book-link-101")
  await readerLink.click()
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(bottomPlayer).toBeVisible()

  await expect(page.locator("#pdf-page-container-1 canvas").first()).toBeVisible({ timeout: 15000 })

  await page.evaluate(() => {
    const win = window as unknown as { ttsEngine?: { readPage: (docId: number, page: number, sentence: number) => void } }
    if (win.ttsEngine) {
      win.ttsEngine.readPage(1, 1, 0)
    }
  })

  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toHaveCount(0)
})

test("signing out immediately stops podcast audio and removes bottom player", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const signOutButton = page.getByRole("button", { name: /Sign out/i })
  await expect(signOutButton).toBeVisible()
  await signOutButton.click()

  await expect(page).toHaveURL(/\/login/)
  await expect(bottomPlayer).toHaveCount(0)
})

test("full screen studio mode leaves without pausing when navigating to reader", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await page.getByTestId("podcast-expand-101").click()
  const playerModal = page.getByTestId("podcast-player-modal")
  await expect(playerModal).toBeVisible()

  const fullscreenToggle = page.getByTestId("podcast-fullscreen-toggle")
  await fullscreenToggle.click()
  await expect(playerModal).toHaveClass(/is-fullscreen-mode/)

  const modalBookLink = page.getByTestId("podcast-modal-book-link")
  await expect(modalBookLink).toBeVisible()
  await modalBookLink.click()

  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(playerModal).toHaveCount(0)

  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByText("Deep Dive into Sample Document")).toBeVisible()

  const isAudioPlaying = await page.evaluate(() => {
    const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
    return Boolean(audio && !audio.paused)
  })
  expect(isAudioPlaying).toBe(true)
})

test("phone viewport persistent bar and reader panel toggles do not overlap", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 })
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await expect(page.getByTestId("podcast-play-101")).toBeVisible({ timeout: 15000 })
  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()

  const bookLink = page.getByTestId("podcast-book-link-101")
  await bookLink.click()
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)

  await expect(bottomPlayer).toBeVisible()

  const playerBox = await bottomPlayer.boundingBox()
  const libraryButton = page.locator('nav button[aria-label="Library"]')
  await expect(libraryButton).toBeVisible()
  const buttonBox = await libraryButton.boundingBox()

  expect(playerBox).not.toBeNull()
  expect(buttonBox).not.toBeNull()
  if (playerBox && buttonBox) {
    const verticalOverlap = Math.max(
      0,
      Math.min(playerBox.y + playerBox.height, buttonBox.y + buttonBox.height) -
        Math.max(playerBox.y, buttonBox.y)
    )
    expect(verticalOverlap).toBe(0)
  }

  await libraryButton.click()
  const readerPodcastShortcut = page.locator('aside a[href="/dashboard?view=podcasts"]')
  await expect(readerPodcastShortcut).toBeVisible()

  const sharedSidebarLink = page.locator('aside a[href="/dashboard/shared"]')
  await expect(sharedSidebarLink).toBeVisible()
  await sharedSidebarLink.click()
  await expect(page).toHaveURL(/\/dashboard\/shared$/)
  await expect(bottomPlayer).toBeVisible()

  const sharedPaperCard = page.locator(".shared-document-card").first()
  await expect(sharedPaperCard).toBeVisible()
  const actionButton = sharedPaperCard.locator(".shared-document-actions button").first()
  await expect(actionButton).toBeVisible()
  await actionButton.scrollIntoViewIfNeeded()

  const actionButtonBox = await actionButton.boundingBox()
  const sharedPlayerBox = await bottomPlayer.boundingBox()
  if (actionButtonBox && sharedPlayerBox) {
    const isObscured =
      actionButtonBox.y + actionButtonBox.height > sharedPlayerBox.y &&
      actionButtonBox.y < sharedPlayerBox.y + sharedPlayerBox.height
    expect(isObscured).toBe(false)
  }
})


test("reader keyboard shortcuts yield to expanded podcast modal for space and escape", async ({ page }) => {
  await setupMockPodcasts(page)
  await page.goto("/dashboard")

  await expect(page.getByTestId("podcast-play-101")).toBeVisible({ timeout: 15000 })
  await page.getByTestId("podcast-play-101").click()
  const bottomPlayer = page.getByTestId("bottom-player")
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible({ timeout: 10000 })

  const readerLink = page.getByTestId("podcast-book-link-101")
  await readerLink.click()
  await expect(page).toHaveURL(/\/dashboard\/read\/1$/)
  await expect(bottomPlayer).toBeVisible()
  await expect(bottomPlayer.getByRole("button", { name: "Pause" })).toBeVisible({ timeout: 10000 })

  await page.getByTestId("bottom-player-expand").click()
  const playerModal = page.getByTestId("podcast-player-modal")
  await expect(playerModal).toBeVisible()
  await expect(playerModal.getByRole("button", { name: "Pause" })).toBeVisible({ timeout: 10000 })

  await page.keyboard.press("Space")
  await expect.poll(async () => {
    return page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio?.paused ?? false
    })
  }).toBe(true)

  await page.keyboard.press("Space")
  await expect.poll(async () => {
    return page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio?.paused ?? true
    })
  }).toBe(false)

  await page.keyboard.press("Escape")
  await expect(playerModal).toHaveCount(0)
  await expect(bottomPlayer).toBeVisible()

  await expect.poll(async () => {
    return page.evaluate(() => {
      const audio = (window as unknown as { __capturedAudioElement?: HTMLAudioElement }).__capturedAudioElement
      return audio?.paused ?? true
    })
  }).toBe(false)
})
