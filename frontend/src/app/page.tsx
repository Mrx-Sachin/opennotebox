"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"

export default function HomePage() {
  const router = useRouter()

  useEffect(() => {
    const token = localStorage.getItem("token")
    if (token) {
      router.push("/dashboard")
    }
  }, [router])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
      <div className="text-center max-w-2xl px-8">
        <div className="mb-8">
          <div className="w-20 h-20 bg-blue-600 rounded-2xl mx-auto flex items-center justify-center shadow-xl mb-6">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-10 w-10 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.246 18 16.5 18"
              />
            </svg>
          </div>
          <h1 className="text-5xl font-bold text-gray-900 mb-4">OpenNoteBox</h1>
          <p className="text-xl text-gray-600 mb-8">
            AI-powered document reader. Upload PDFs, get AI summaries, chat with
            your documents, and listen with synced text-to-speech highlighting.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
          <div className="bg-white p-6 rounded-xl shadow-md">
            <h3 className="font-semibold text-gray-800 mb-2">Smart Summaries</h3>
            <p className="text-sm text-gray-600">
              Page-level and section-level AI summaries from any PDF document
            </p>
          </div>
          <div className="bg-white p-6 rounded-xl shadow-md">
            <h3 className="font-semibold text-gray-800 mb-2">Chat with Docs</h3>
            <p className="text-sm text-gray-600">
              Ask questions and get answers with source citations from your content
            </p>
          </div>
          <div className="bg-white p-6 rounded-xl shadow-md">
            <h3 className="font-semibold text-gray-800 mb-2">TTS Playback</h3>
            <p className="text-sm text-gray-600">
              Listen to summaries and raw text with synced word highlighting
            </p>
          </div>
        </div>

        <div className="flex gap-4 justify-center">
          <button
            onClick={() => router.push("/login")}
            className="px-8 py-3 bg-blue-600 text-white rounded-lg font-semibold shadow hover:bg-blue-700 transition-colors"
          >
            Sign In
          </button>
          <button
            onClick={() => router.push("/register")}
            className="px-8 py-3 bg-white text-blue-600 rounded-lg font-semibold shadow border border-blue-200 hover:bg-blue-50 transition-colors"
          >
            Create Account
          </button>
        </div>
      </div>
    </div>
  )
}
