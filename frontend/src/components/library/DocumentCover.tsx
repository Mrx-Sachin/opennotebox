"use client"

import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { BookOpen } from "lucide-react"
import { getDocumentPreview, PreviewPending } from "@/services/libraryApi"
import type { Document } from "@/services/types"

const COVER_COLORS = ["#2e4354", "#553b59", "#30514e", "#63453a", "#3d4166", "#51533a"]

export function displayTitle(title: string) {
  return title.replace(/\.(pdf|epub|docx|pptx|txt|md)$/i, "").replace(/_/g, " ")
}

export default function DocumentCover({ document }: { document: Document }) {
  const container = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const { data } = useQuery({
    queryKey: ["document-preview", document.user_id, document.id],
    queryFn: ({ signal }) => getDocumentPreview(document.id, signal),
    enabled: visible && document.format === "pdf",
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
    retry: (count, error) => error instanceof PreviewPending && count < 10,
    retryDelay: 2000,
  })

  useEffect(() => {
    const element = container.current
    if (!element) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: "120px" })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!data) return
    const url = URL.createObjectURL(data)
    setImageUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [data])

  const title = displayTitle(document.title)
  return (
    <div ref={container} className="document-cover" style={{ backgroundColor: COVER_COLORS[document.id % COVER_COLORS.length] }} aria-hidden="true">
      {imageUrl ? (
        // Authenticated covers use a temporary blob URL and have fixed dimensions.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={imageUrl} alt="" className="document-cover-image" />
      ) : (
        <div className="document-cover-type">
          <BookOpen size={19} strokeWidth={1.4} />
          <span className="document-cover-title">{title}</span>
          <span className="document-cover-format">{document.format.toUpperCase()}</span>
        </div>
      )}
    </div>
  )
}
