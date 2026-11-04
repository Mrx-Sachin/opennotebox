"use client"

import { useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  Mic,
  Layers,
  Sparkles,
  PanelLeft,
  PanelRight,
} from "lucide-react"
import ChatPanel from "@/components/chat/ChatPanel"
import PodcastPanel from "./PodcastPanel"
import FlashcardPanel from "./FlashcardPanel"
import { useReader } from "@/stores/reader"

interface AiPanelProps {
  docId: number
  docTitle: string
}

type AiTab = "chat" | "podcasts" | "cards"

export default function AiPanel({ docId, docTitle }: AiPanelProps) {
  const [activeTab, setActiveTab] = useState<AiTab>("chat")
  const chatSide = useReader((s) => s.chatSide)
  const toggleChatSide = useReader((s) => s.toggleChatSide)
  const toggleChat = useReader((s) => s.toggleChat)

  return (
    <div className="flex h-full flex-col">
      <div className="h-11 flex items-center justify-between border-b border-surface px-2.5 bg-void/60">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={toggleChat}
            title="Collapse assistant"
            aria-label="Collapse assistant"
            className="flex h-7 w-7 items-center justify-center rounded-md text-cream-dim transition-[background-color,color] duration-150 hover:bg-surface hover:text-cream"
          >
            {chatSide === "right" ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </button>
          <button
            type="button"
            onClick={toggleChatSide}
            title={chatSide === "right" ? "Move panel to the left" : "Move panel to the right"}
            aria-label={chatSide === "right" ? "Move panel to the left" : "Move panel to the right"}
            className="flex h-7 w-7 items-center justify-center rounded-md text-cream-dim transition-[background-color,color] duration-150 hover:bg-surface hover:text-cream"
          >
            {chatSide === "right" ? (
              <PanelLeft className="h-3.5 w-3.5" />
            ) : (
              <PanelRight className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        <div
          role="tablist"
          aria-label="Assistant modes"
          className="flex items-center rounded-full border border-surface bg-void/90 p-0.5 shadow-sm"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "chat"}
            onClick={() => setActiveTab("chat")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-[background-color,color,border-color,box-shadow] duration-150 ${
              activeTab === "chat"
                ? "border border-white/10 bg-surface text-cream shadow-sm"
                : "border border-transparent text-cream-dim hover:bg-surface/30 hover:text-cream"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5 text-sky-400" />
            <span>Chat</span>
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "podcasts"}
            onClick={() => setActiveTab("podcasts")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-[background-color,color,border-color,box-shadow] duration-150 ${
              activeTab === "podcasts"
                ? "border border-purple-500/30 bg-purple-950/50 text-purple-200 shadow-sm"
                : "border border-transparent text-cream-dim hover:bg-surface/30 hover:text-cream"
            }`}
          >
            <Mic className="h-3.5 w-3.5 text-purple-400" />
            <span>Podcasts</span>
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "cards"}
            onClick={() => setActiveTab("cards")}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-[background-color,color,border-color,box-shadow] duration-150 ${
              activeTab === "cards"
                ? "border border-emerald-500/30 bg-emerald-950/50 text-emerald-200 shadow-sm"
                : "border border-transparent text-cream-dim hover:bg-surface/30 hover:text-cream"
            }`}
          >
            <Layers className="h-3.5 w-3.5 text-emerald-400" />
            <span>Cards</span>
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === "chat" && <ChatPanel docId={docId} />}
        {activeTab === "podcasts" && (
          <div className="h-full overflow-y-auto reader-scroll p-4 space-y-3">
            <PodcastPanel docId={docId} docTitle={docTitle} />
          </div>
        )}
        {activeTab === "cards" && (
          <div className="h-full overflow-y-auto reader-scroll p-4 space-y-3">
            <FlashcardPanel docId={docId} />
          </div>
        )}
      </div>
    </div>
  )
}
