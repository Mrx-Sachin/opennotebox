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

  // Stop TTS playback and signal GPU release when the tab is hidden or closed.
  // This is what frees GPU VRAM when the user navigates away, closes the tab,
  // or switches away from the app - the TTS service's idle timer then does
  // the actual Kokoro session teardown after its timeout window.
  useEffect(() => {
    const cleanup = () => {
      ttsEngine.stop()
      releaseTtsGpu()
    }
    window.addEventListener("beforeunload", cleanup)
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") cleanup()
    })
    return () => {
      window.removeEventListener("beforeunload", cleanup)
      document.removeEventListener("visibilitychange", cleanup)
    }
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}
