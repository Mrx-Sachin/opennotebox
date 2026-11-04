import type { Page } from "@playwright/test"

export const libraryDocuments = Array.from({ length: 29 }, (_, index) => ({
  id: index + 1,
  user_id: 101,
  notebook_id: null as number | null,
  title: index === 0 ? "e2e-sample.pdf" : index === 1 ? "Research Methods.epub" : `Reading Notes ${String(index + 1).padStart(2, "0")}.pdf`,
  format: index === 1 ? "epub" : "pdf",
  file_size: 24000,
  num_pages: index === 0 ? 2 : 60,
  processing_status: index === 28 ? "pending" : index === 27 ? "failed" : "completed",
  created_at: `2026-10-${String(29 - index).padStart(2, "0")}T00:00:00Z`,
  last_read_page: index < 3 ? 1 : null,
  last_read_at: index < 3 ? `2026-10-30T0${3 - index}:00:00Z` : null,
  last_read_position: null,
}))

export async function mockLibrary(page: Page, options: { empty?: boolean; error?: boolean; notebooks?: boolean } = {}) {
  const documents = options.empty ? [] : libraryDocuments.map((document) => ({ ...document }))
  let notebooks = options.notebooks ? [{ id: 1, name: "Research", description: "Related research papers", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" }] : []
  let unfiled = { name: "Unfiled", description: "Documents waiting for a notebook." }
  if (options.notebooks) documents.slice(0, 2).forEach((document) => { document.notebook_id = 1 })
  await page.addInitScript(() => localStorage.setItem("token", "library-test-token"))
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 101, username: "library.reader", is_admin: false, created_at: "2026-10-01T00:00:00Z" } })
    }
    if (url.pathname === "/api/notebooks/move-documents") {
      const body = request.postDataJSON()
      documents.filter((document) => body.document_ids.includes(document.id)).forEach((document) => { document.notebook_id = body.notebook_id })
      return route.fulfill({ json: { moved: body.document_ids.length } })
    }
    if (url.pathname === "/api/notebooks/unfiled") {
      if (request.method() === "PATCH") {
        Object.assign(unfiled, request.postDataJSON())
        return route.fulfill({ json: unfiled })
      }
      return route.fulfill({ json: unfiled })
    }
    if (url.pathname === "/api/notebooks") {
      if (request.method() === "POST") {
        const body = request.postDataJSON()
        const notebook = { id: Math.max(0, ...notebooks.map((item) => item.id)) + 1, name: body.name, description: body.description, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" }
        notebooks.push(notebook)
        return route.fulfill({ json: { ...notebook, document_count: 0 } })
      }
      return route.fulfill({ json: notebooks.map((notebook) => ({ ...notebook, document_count: documents.filter((document) => document.notebook_id === notebook.id).length })) })
    }
    if (/^\/api\/notebooks\/\d+$/.test(url.pathname)) {
      const id = Number(url.pathname.split("/").pop())
      if (request.method() === "DELETE") {
        notebooks = notebooks.filter((notebook) => notebook.id !== id)
        documents.filter((document) => document.notebook_id === id).forEach((document) => { document.notebook_id = null })
        return route.fulfill({ status: 204 })
      }
      const notebook = notebooks.find((notebook) => notebook.id === id)!
      Object.assign(notebook, request.postDataJSON())
      return route.fulfill({ json: { ...notebook, document_count: documents.filter((document) => document.notebook_id === id).length } })
    }
    if (url.pathname === "/api/documents/library") {
      if (options.error) return route.fulfill({ status: 503, json: { detail: "Unavailable" } })
      const notebookId = Number(url.searchParams.get("notebook_id"))
      const scope = documents.filter((document) => notebookId ? document.notebook_id === notebookId : url.searchParams.get("unfiled") === "true" ? document.notebook_id == null : true)
      let results = [...scope]
      const search = url.searchParams.get("search")?.toLowerCase()
      const format = url.searchParams.get("format")
      const shelf = url.searchParams.get("shelf")
      if (search) results = results.filter((document) => document.title.toLowerCase().includes(search))
      if (format) results = results.filter((document) => document.format === format)
      if (shelf === "reading") results = results.filter((document) => document.last_read_at)
      if (shelf === "unread") results = results.filter((document) => !document.last_read_at)
      if (shelf === "processing") results = results.filter((document) => document.processing_status === "pending")
      if (shelf === "failed") results = results.filter((document) => document.processing_status === "failed")
      if (url.searchParams.get("sort") === "title") results.sort((a, b) => a.title.localeCompare(b.title))
      const offset = Number(url.searchParams.get("offset") || 0)
      const limit = Number(url.searchParams.get("limit") || 24)
      return route.fulfill({ json: {
        documents: results.slice(offset, offset + limit), total: results.length,
        library_total: scope.length, reading_count: scope.filter((document) => document.last_read_at).length,
        processing_count: scope.filter((document) => document.processing_status === "pending").length, failed_count: scope.filter((document) => document.processing_status === "failed").length,
        formats: scope.reduce<Record<string, number>>((formats, document) => ({ ...formats, [document.format]: (formats[document.format] || 0) + 1 }), {}), recent: scope.filter((document) => document.last_read_at).slice(0, 3),
      } })
    }
    if (url.pathname === "/api/documents") return route.fulfill({ json: documents.filter((document) => url.searchParams.has("notebook_id") ? document.notebook_id === Number(url.searchParams.get("notebook_id")) : url.searchParams.get("unfiled") === "true" ? document.notebook_id == null : true) })
    if (url.pathname.endsWith("/preview")) return route.fulfill({ status: 204 })
    if (url.pathname === "/api/documents/1") return route.fulfill({ json: documents[0] })
    if (url.pathname === "/api/documents/1/file") return route.fulfill({ path: "fixtures/e2e-sample.pdf", contentType: "application/pdf" })
    if (url.pathname === "/api/settings") return route.fulfill({ json: { llm: { active: "omniroute", backends: [] }, tts: { default_voice: "test" }, export: { enabled: false } } })
    return route.fulfill({ json: [] })
  })
}
