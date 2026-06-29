import { useState, useEffect, useRef, useCallback } from "react"

export default function useProjectSocket(projectId) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)

  const connect = useCallback(() => {
    if (!projectId) return

    const protocol = location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${location.host}/ws/${projectId}`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === "pong") return
        setEvents(prev => [...prev.slice(-50), data])
      } catch {}
    }

    ws.onclose = () => {
      setConnected(false)
      reconnectRef.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => ws.close()
  }, [projectId])

  useEffect(() => {
    connect()
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
    }
  }, [connect])

  const lastEvent = events.length > 0 ? events[events.length - 1] : null

  const progress = lastEvent?.type === "task_progress" ? lastEvent.progress : null
  const taskType = lastEvent?.task_type || null
  const isRunning = lastEvent?.type === "task_started" || lastEvent?.type === "task_progress"
  const isDone = lastEvent?.type === "task_completed"
  const isFailed = lastEvent?.type === "task_failed"

  const clearEvents = () => setEvents([])

  return { connected, events, lastEvent, progress, taskType, isRunning, isDone, isFailed, clearEvents }
}
