import type { Page } from "@playwright/test"
import { mockLibrary } from "./library-fixture"

export const sharedDocuments = Array.from({ length: 26 }, (_, index) => {
  const id = index + 1
  const title = id === 1 ? "Attention Is All You Need" : id === 2 ? "Research Methods" : id === 3 ? "Designing with Type" : `Shared paper ${String(id).padStart(2, "0")}`
  const type = id === 2 || id % 4 === 0 ? "textbook" : id === 3 || id % 4 === 1 ? "manual" : "paper"
  const topics = id === 1 ? ["machine learning", "transformers"] : id === 2 ? ["research", "methods"] : id % 2 ? ["design", "typography"] : ["machine learning", "systems"]
  return {
    id,
    title,
    author: id === 1 ? "A. Research" : `Author ${id}`,
    description: id === 1 ? "A foundational paper on attention-based sequence models." : `A team-selected guide about ${topics[0]}.`,
    topics,
    document_type: type,
    language: "English",
    num_pages: 12 + id,
    file_size: 24000 + id * 100,
    shared_at: "2026-10-01T00:00:00Z",
    added: id === 2,
  }
})

export async function mockSharedLibrary(page: Page, options: { readOnly?: boolean; canShare?: boolean } = {}) {
  const updates: Array<{ documentId: number; body: Record<string, unknown> }> = []
  const additions: Array<{ document_ids: number[]; notebook_id: number | null }> = []
  await mockLibrary(page, { notebooks: true })
  await page.route("**/api/shared-library**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.headers()["authorization"] !== "Bearer library-test-token") {
      return route.fulfill({ status: 401, json: { detail: "Authentication required" } })
    }
    if (url.pathname === "/api/shared-library" && request.method() === "GET") {
      let results = sharedDocuments.map((document) => ({ ...document, topics: [...document.topics] }))
      const search = url.searchParams.get("search")?.toLowerCase()
      const topic = url.searchParams.get("topic")
      const documentType = url.searchParams.get("document_type")
      if (search) results = results.filter((document) => `${document.title} ${document.author} ${document.description} ${document.topics.join(" ")}`.toLowerCase().includes(search))
      if (topic) results = results.filter((document) => document.topics.includes(topic))
      if (documentType) results = results.filter((document) => document.document_type === documentType)
      const notebookId = url.searchParams.get("notebook_id")
      if (notebookId) results = results.map((document, index) => index === 0 ? { ...document, reason: "Related to Research" } : document)
      const offset = Number(url.searchParams.get("offset") || 0)
      const limit = Number(url.searchParams.get("limit") || 24)
      return route.fulfill({ json: {
        documents: results.slice(offset, offset + limit),
        total: results.length,
        topics: [...new Set(sharedDocuments.flatMap((document) => document.topics))].sort(),
        document_types: [...new Set(sharedDocuments.map((document) => document.document_type))].sort(),
      } })
    }
    if (url.pathname === "/api/shared-library/add" && request.method() === "POST") {
      const body = request.postDataJSON() as { document_ids: number[]; notebook_id: number | null }
      additions.push(body)
      let added = 0
      let alreadyAdded = 0
      for (const id of body.document_ids) {
        const document = sharedDocuments.find((item) => item.id === id)
        if (!document || document.added) {
          alreadyAdded += 1
          continue
        }
        document.added = true
        added += 1
      }
      return route.fulfill({ json: { added, already_added: alreadyAdded, document_ids: body.document_ids } })
    }
    if (/^\/api\/shared-library\/\d+\/file$/.test(url.pathname)) {
      return route.fulfill({ path: "fixtures/e2e-sample.pdf", contentType: "application/pdf" })
    }
    if (/^\/api\/shared-library\/\d+\/preview$/.test(url.pathname)) {
      return route.fulfill({ status: 204 })
    }
    if (/^\/api\/shared-library\/\d+$/.test(url.pathname) && request.method() === "PATCH") {
      const id = Number(url.pathname.split("/").pop())
      const document = sharedDocuments.find((item) => item.id === id)
      const body = request.postDataJSON() as Record<string, unknown>
      updates.push({ documentId: id, body })
      return route.fulfill({ json: { ...document, ...body } })
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } })
  })
  await page.route("**/api/documents/1", async (route) => {
    if (route.request().method() !== "GET") return route.fallback()
    return route.fulfill({ json: {
      ...{
        id: 1, user_id: 101, notebook_id: null, title: "e2e-sample.pdf", format: "pdf", file_size: 24000,
        num_pages: 2, processing_status: "completed", created_at: "2026-10-01T00:00:00Z", last_read_page: null,
      },
      can_share: options.canShare ?? true,
      is_shared: false,
      read_only: options.readOnly ?? false,
    } })
  })
  return { updates, additions }
}
