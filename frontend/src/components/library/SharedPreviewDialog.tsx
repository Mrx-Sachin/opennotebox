"use client"

import { useEffect, useRef, useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, X } from "lucide-react"
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist"
import { apiUrl } from "@/lib/apiBase"
import type { SharedDocument } from "@/services/types"

export default function SharedPreviewDialog({ document, onClose }: {
  document: SharedDocument
  onClose: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const pdfRef = useRef<PDFDocumentProxy | null>(null)
  const renderRef = useRef<RenderTask | null>(null)
  const [pageCount, setPageCount] = useState(0)
  const [pageNumber, setPageNumber] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    let active = true
    let pdf: PDFDocumentProxy | null = null

    async function openPdf() {
      try {
        const pdfjs = await import("pdfjs-dist")
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs"
        const token = localStorage.getItem("token")
        pdf = await pdfjs.getDocument({
          url: apiUrl(`/api/shared-library/${document.id}/file`),
          httpHeaders: token ? { Authorization: `Bearer ${token}` } : undefined,
          disableAutoFetch: true,
          disableStream: true,
          rangeChunkSize: 64 * 1024,
        }).promise
        if (!active) {
          await pdf.destroy()
          return
        }
        pdfRef.current = pdf
        setPageCount(pdf.numPages)
        setLoading(false)
      } catch {
        if (active) {
          setError("This paper could not be previewed. Please try again.")
          setLoading(false)
        }
      }
    }

    void openPdf()
    return () => {
      active = false
      renderRef.current?.cancel()
      renderRef.current = null
      pdfRef.current = null
      if (pdf) void pdf.destroy()
    }
  }, [document.id])

  useEffect(() => {
    let active = true
    let pageProxy: PDFPageProxy | null = null

    async function renderPage() {
      const pdf = pdfRef.current
      const canvas = canvasRef.current
      const container = viewportRef.current
      if (!pdf || !canvas || !container || !pageCount) return
      setLoading(true)
      setError("")
      renderRef.current?.cancel()
      try {
        pageProxy = await pdf.getPage(pageNumber)
        if (!active) return
        const natural = pageProxy.getViewport({ scale: 1 })
        const availableWidth = Math.max(240, container.clientWidth - 32)
        const areaLimitScale = Math.sqrt(10_000_000 / (natural.width * natural.height))
        const scale = Math.min(1.4, availableWidth / natural.width, areaLimitScale)
        const viewport = pageProxy.getViewport({ scale })
        const pixelRatio = Math.min(
          1.5,
          window.devicePixelRatio || 1,
          Math.sqrt(12_000_000 / (viewport.width * viewport.height)),
        )
        canvas.width = Math.ceil(viewport.width * pixelRatio)
        canvas.height = Math.ceil(viewport.height * pixelRatio)
        canvas.style.width = `${Math.ceil(viewport.width)}px`
        canvas.style.height = `${Math.ceil(viewport.height)}px`
        const context = canvas.getContext("2d", { alpha: false })
        if (!context) throw new Error("Canvas rendering is unavailable")
        const task = pageProxy.render({
          canvasContext: context,
          viewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
          background: "#ffffff",
        })
        renderRef.current = task
        await task.promise
      } catch (caught) {
        if (active && !(caught instanceof Error && caught.name === "RenderingCancelledException")) {
          setError("This page could not be rendered. Please try again.")
        }
      } finally {
        if (active) setLoading(false)
        pageProxy?.cleanup()
        pageProxy = null
      }
    }

    void renderPage()
    return () => {
      active = false
      renderRef.current?.cancel()
      renderRef.current = null
      pageProxy?.cleanup()
    }
  }, [pageNumber, pageCount])

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="notebook-dialog-overlay shared-preview-overlay" />
        <Dialog.Content className="shared-preview-dialog">
          <div className="shared-preview-heading">
            <div>
              <Dialog.Title>{document.title}</Dialog.Title>
              <Dialog.Description>{document.author ? `${document.author} · ` : ""}{pageCount || document.num_pages || "PDF"} pages · {document.document_type}</Dialog.Description>
            </div>
            <Dialog.Close aria-label="Close preview"><X size={19} /></Dialog.Close>
          </div>
          <div ref={viewportRef} className="shared-preview-viewport" aria-busy={loading}>
            {error ? <div className="shared-preview-error" role="alert"><AlertCircle size={20} /><p>{error}</p></div> : (
              <>
                {loading && <div className="shared-preview-loading"><Loader2 size={22} className="animate-spin" /><span>Loading page {pageNumber}</span></div>}
                <canvas ref={canvasRef} className="shared-preview-canvas" aria-label={`Page ${pageNumber} of ${pageCount || document.num_pages}`} />
              </>
            )}
          </div>
          <nav className="shared-preview-controls" aria-label="Preview pages">
            <button type="button" aria-label="Previous page" disabled={pageNumber <= 1 || loading} onClick={() => setPageNumber((current) => current - 1)}><ChevronLeft size={17} />Previous</button>
            <span>Page {pageNumber} of {pageCount || document.num_pages || "…"}</span>
            <button type="button" aria-label="Next page" disabled={!pageCount || pageNumber >= pageCount || loading} onClick={() => setPageNumber((current) => current + 1)}>Next<ChevronRight size={17} /></button>
          </nav>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
