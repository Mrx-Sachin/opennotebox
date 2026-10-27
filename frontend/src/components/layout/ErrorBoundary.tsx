"use client"

import { Component, type ReactNode } from "react"

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

/** Catches render-time crashes in any child component and displays a
    recovery screen instead of a white page. */
export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
          <span className="text-2xl">&#x26A0;</span>
          <p className="text-sm text-cream">
            Something went wrong. Reloading may help.
          </p>
          <button
            onClick={() => window.location.reload()}
            className="rounded-lg border border-white/20 px-3 py-1.5 text-[13px] text-cream transition-colors hover:bg-surface"
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
