"use client"

import { ReactNode } from "react"
import { BookOpen } from "lucide-react"

/** Centered dark card shell shared by /login and /register - matches the
    TopBar brand mark and glass/gradient language used throughout the
    reader instead of the old light bg-gray-50/bg-white auth screens. */
export default function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-void px-4">
      <div className="glass w-full max-w-sm rounded-2xl border border-surface p-8 shadow-glow">
        <div className="mb-7 flex flex-col items-center text-center">
          <div className="gradient-primary mb-3 flex h-11 w-11 items-center justify-center rounded-xl shadow-glow">
            <BookOpen className="h-5 w-5 text-white" />
          </div>
          <h1 className="font-display text-xl font-bold tracking-tight text-cream">{title}</h1>
          <p className="mt-1 text-sm text-cream-dim">{subtitle}</p>
        </div>
        {children}
      </div>
    </div>
  )
}
