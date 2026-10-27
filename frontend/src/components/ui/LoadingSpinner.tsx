"use client"

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg"
}

/** Inline spinner for async operations. Sizes map to the most common
    patterns found across the app. */
const sizes = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-8 w-8 border-2",
}

export default function LoadingSpinner({ size = "lg" }: LoadingSpinnerProps) {
  return (
    <div
      className={`animate-spin rounded-full border-indigo border-t-transparent ${sizes[size]}`}
      role="status"
      aria-label="Loading"
    />
  )
}
