"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { ttsEngine } from "@/services/ttsEngine"

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

  // A tab owns playback, not the team's shared synthesis engine.
  useEffect(() => {
    const cleanup = () => {
      ttsEngine.stop()
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
