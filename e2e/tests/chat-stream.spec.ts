import { expect, test } from "@playwright/test"

test.describe("streaming chat", () => {
  test("shows generated text before final citations and saves one user turn", async ({ page }) => {
    const document = {
      id: 1,
      user_id: 71,
      title: "E2E Sample Paper.pdf",
      format: "pdf",
      file_size: 8192,
      num_pages: 2,
      processing_status: "completed",
      created_at: "2026-01-01T00:00:00Z",
      last_read_page: null,
      last_read_position: null,
      last_read_at: null,
    }
    const user = {
      id: 71,
      username: "e2e.stream",
      is_admin: false,
      created_at: "2026-01-01T00:00:00Z",
    }
    await page.addInitScript(() => localStorage.setItem("token", "e2e-stream-token"))
    await page.route("**/api/**", async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.pathname === "/api/auth/me") {
        await route.fulfill({ json: user })
      } else if (url.pathname === "/api/documents") {
        await route.fulfill({ json: [document] })
      } else if (url.pathname === "/api/documents/1") {
        await route.fulfill({ json: document })
      } else if (url.pathname === "/api/documents/1/file") {
        await route.fulfill({
          path: "fixtures/e2e-sample.pdf",
          contentType: "application/pdf",
        })
      } else if (url.pathname === "/api/settings") {
        await route.fulfill({
          json: {
            llm: {
              active: "omniroute",
              backends: [{ name: "omniroute", base_url: "http://mock/v1", model: "mock" }],
            },
            tts: { default_voice: "test" },
            export: { enabled: false },
          },
        })
      } else if (url.pathname === "/api/settings/llm/health") {
        await route.fulfill({
          json: { active: "omniroute", backends: [{ name: "omniroute", status: "healthy" }] },
        })
      } else if (url.pathname === "/api/chat/sessions" && request.method() === "GET") {
        await route.fulfill({ json: [] })
      } else if (url.pathname === "/api/chat/sessions" && request.method() === "POST") {
        await route.fulfill({
          json: {
            id: 903,
            user_id: user.id,
            title: "New Chat",
            document_ids: null,
            page_scope: null,
            created_at: "2026-01-01T00:00:00Z",
          },
        })
      } else {
        await route.fulfill({ json: [] })
      }
    })

    await page.goto("/dashboard/read/1")
    await expect(page.getByText("E2E Sample Paper", { exact: false }).first()).toBeVisible()

    await page.evaluate(() => {
      const streamWindow = window as Window & {
        __chatStreamRequestCount?: number
        __chatStreamPayload?: { content?: string }
      }
      streamWindow.__chatStreamRequestCount = 0
      const originalFetch = window.fetch.bind(window)
      window.fetch = async (input, init) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url
        if (!url.includes("/messages/stream")) return originalFetch(input, init)

        streamWindow.__chatStreamRequestCount! += 1
        streamWindow.__chatStreamPayload = JSON.parse(String(init?.body || "{}"))
        const sessionId = Number(url.match(/\/sessions\/(\d+)\//)?.[1] || 1)
        const encoder = new TextEncoder()
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode('data: {"type":"status","stage":"retrieving"}\n\n'),
            )

            const firstEvent = 'data: {"type":"token","content":"The first token"}\n\n'
            controller.enqueue(encoder.encode(firstEvent.slice(0, 22)))
            window.setTimeout(() => {
              controller.enqueue(encoder.encode(firstEvent.slice(22)))
            }, 40)
            window.setTimeout(() => {
              controller.enqueue(
                encoder.encode(
                  'data: {"type":"token","content":" arrives live."}\n\n',
                ),
              )
            }, 900)
            window.setTimeout(() => {
              controller.enqueue(
                encoder.encode(
                  `data: {"type":"final","message":{"id":987654,"conversation_id":${sessionId},"role":"assistant","content":"The first token arrives live.","citations":[],"token_count":5}}\n\n`,
                ),
              )
              controller.close()
            }, 1000)
          },
        })
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        })
      }
    })

    const assistantToggle = page.getByRole("button", { name: "Assistant" }).first()
    if ((await assistantToggle.getAttribute("aria-pressed")) !== "true") {
      await assistantToggle.click()
    }
    const composer = page.getByPlaceholder("Ask about this document…")
    await expect(composer).toBeVisible()

    const question = "How does the mocked stream work?"
    await composer.fill(question)
    await page.getByTitle("Send").click()

    await expect(page.getByText(question, { exact: true })).toHaveCount(1)
    await expect(page.getByText("The first token", { exact: false })).toBeVisible()
    await expect(page.getByText("arrives live.", { exact: false })).not.toBeVisible()
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible()
    await expect(page.getByText("arrives live.", { exact: false })).toBeVisible()
    await expect(page.getByText("The first token arrives live.", { exact: false })).toBeVisible()

    const request = await page.evaluate(() => {
      const streamWindow = window as Window & {
        __chatStreamRequestCount?: number
        __chatStreamPayload?: { content?: string }
      }
      return {
        count: streamWindow.__chatStreamRequestCount,
        content: streamWindow.__chatStreamPayload?.content,
      }
    })
    expect(request).toEqual({ count: 1, content: question })
  })
})
