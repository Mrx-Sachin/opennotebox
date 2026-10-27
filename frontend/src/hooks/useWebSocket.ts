"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { wsBase } from "@/lib/apiBase"

const MIN_DELAY = 1000
const MAX_DELAY = 32000
const JITTER = 100

export function useWebSocket(docId?: number) {
  const [progress, setProgress] = useState<{ task: string; value: number; message?: string } | null>(null)
  const reconnectAttempt = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const mountedRef = useRef(true)

  const connect = useCallback(() => {
    const token = localStorage.getItem("token")
    if (!token) return

    // Pass auth via WebSocket subprotocol header (not query params) to avoid
    // token leaking into server/proxy access logs and browser history.
    const ws = new WebSocket(`${wsBase()}/ws/progress`, [`token ${token}`])
    wsRef.current = ws

    ws.onopen = () => {
      reconnectAttempt.current = 0
    }

    ws.onmessage = (event) => {
      let data
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }
      if (data.doc_id === docId) {
        setProgress({
          task: data.task,
          value: data.progress || 0,
          message: data.message,
        })
      }
    }

    // Only onclose schedules reconnection - onerror always fires before
    // onclose, so letting both call scheduleReconnect would set up two
    // independent timers and create duplicate WebSocket connections.
    ws.onclose = () => {
      setProgress(null)

      const token = localStorage.getItem("token")
      if (!token || !mountedRef.current) return

      const delay = Math.min(MIN_DELAY * Math.pow(2, reconnectAttempt.current), MAX_DELAY)
      const jitteredDelay = delay + (Math.random() * JITTER * 2 - JITTER)

      reconnectTimer.current = setTimeout(() => {
        reconnectAttempt.current += 1
        connect()
      }, jitteredDelay)
    }
  }, [docId])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      reconnectAttempt.current = 0
    }
  }, [connect])

  return progress
}
