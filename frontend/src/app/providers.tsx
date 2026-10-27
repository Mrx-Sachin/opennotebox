"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { ttsEngine } from "@/services/ttsEngine"
import { releaseTtsGpu } from "@/services/api"

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            refetchOnWindowFocus: false,
          },
        },
      })
  )

  // Release the GPU only when the tab is actually going away. Dropping the
  // Kokoro session on every visibilitychange (alt-tab, checking a note in
  // another window) forced a 2-4s cold reload on the next click, which is
  // why click-to-audio drifted back over a second. The TTS service still
  // tears the session down after its own idle window.
  useEffect(() => {
    const cleanup = () => {
      ttsEngine.stop()
      releaseTtsGpu()
    }
    window.addEventListener("beforeunload", cleanup)
    return () => {
      window.removeEventListener("beforeunload", cleanup)
    }
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}
