"use client"

import { useState } from "react"
import { Mic, Layers, Sparkles, PanelLeft, PanelRight, Database } from "lucide-react"
import ChatPanel from "@/components/chat/ChatPanel"
import PodcastPanel from "./PodcastPanel"
import FlashcardPanel from "./FlashcardPanel"
import KgPanel from "./KgPanel"
import { useReader } from "@/stores/reader"

interface AiPanelProps {
  docId: number
  docTitle: string
}

type Feature = "podcast" | "flashcards" | "knowledgegraph" | null

export default function AiPanel({ docId, docTitle }: AiPanelProps) {
  const [openFeature, setOpenFeature] = useState<Feature>(null)
  const chatSide = useReader((s) => s.chatSide)
  const toggleChatSide = useReader((s) => s.toggleChatSide)

  const featureBtn = (feature: Exclude<Feature, null>, icon: React.ReactNode, label: string) => {
    const active = openFeature === feature
    return (
      <button
        onClick={() => setOpenFeature(active ? null : feature)}
        className={`flex flex-col items-center gap-1 rounded-lg border py-2.5 text-[11px] font-medium transition-all ${
          active
            ? "border-indigo/60 bg-indigo/10 text-indigo shadow-glow"
            : "border-surface text-cream-dim hover:border-indigo/40 hover:bg-surface/60 hover:text-cream"
        }`}
      >
        {icon}
        {label}
      </button>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-surface p-3">
        <div className="mb-2.5 flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-indigo" />
          <h3 className="font-display flex-1 text-sm font-bold text-cream">AI Assistant</h3>
          <button
            onClick={toggleChatSide}
            title={chatSide === "right" ? "Move panel to the left" : "Move panel to the right"}
            className="rounded p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            {chatSide === "right" ? (
              <PanelLeft className="h-3.5 w-3.5" />
            ) : (
              <PanelRight className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {featureBtn("podcast", <Mic className="h-4 w-4" />, "Podcast")}
          {featureBtn("flashcards", <Layers className="h-4 w-4" />, "Flashcards")}
          {featureBtn("knowledgegraph", <Database className="h-4 w-4" />, "KG")}
        </div>
      </div>

      {openFeature === "podcast" && (
        <PodcastPanel docId={docId} docTitle={docTitle} />
      )}
      {openFeature === "flashcards" && <FlashcardPanel docId={docId} />}
      {openFeature === "knowledgegraph" && <KgPanel docId={docId} />}

      <div className="min-h-0 flex-1">
        <ChatPanel docId={docId} />
      </div>
    </div>
  )
}
