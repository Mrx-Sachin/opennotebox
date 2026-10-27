/// <reference types="next" />
/// <reference types="next/image-types/global" />

import { ReactNode } from "react"

declare global {
  interface Window {
    __NEXT_DATA__: any
  }
}

interface EnvConfig {
  NEXT_PUBLIC_API_URL?: string
  NEXT_PUBLIC_WS_URL?: string
}
