import { useState, useEffect, useRef } from "react"

const SUGGESTIONS = [
  "Bajá el volumen de la música a la mitad",
  "Poné los subtítulos arriba y más grandes",
  "Hacé la narración un 15% más rápida",
  "Cambiá la fuente de video a Pexels",
]

export default function AssistantPanel({ projectId, onClose, onUpdate }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [configured, setConfigured] = useState(true)
  const [error, setError] = useState(null)
  const scrollRef = useRef()

  useEffect(() => {
    fetch("/api/assistant/status")
      .then(r => r.json())
      .then(d => setConfigured(!!d.configured))
      .catch(() => {})
    fetch(`/api/assistant/${projectId}/history`)
      .then(r => r.json())
      .then(d => setMessages(d.history || []))
      .catch(() => {})
  }, [projectId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [messages, sending])

  const send = async (text) => {
    const msg = (text ?? input).trim()
    if (!msg || sending) return
    setError(null)
    setMessages(m => [...m, { role: "user", text: msg }])
    setInput("")
    setSending(true)
    try {
      const res = await fetch(`/api/assistant/${projectId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || `Error ${res.status}`)
      } else {
        setMessages(m => [...m, { role: "assistant", text: data.reply || "(sin respuesta)" }])
        if (data.actions?.length) onUpdate?.()
      }
    } catch (e) {
      setError("Error de conexión con el asistente")
    }
    setSending(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full sm:w-[560px] sm:max-w-[95vw] border border-gray-200 max-h-[85dvh] flex flex-col overflow-hidden">
        <div className="flex justify-between items-center px-5 py-4 border-b border-gray-100">
          <h2 className="m-0 text-base font-medium">🤖 Asistente IA</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        {!configured && (
          <div className="mx-5 mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            El asistente no está configurado (falta ANTHROPIC_API_KEY).
          </div>
        )}

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-3 min-h-[280px]">
          {messages.length === 0 && (
            <div className="text-center py-6">
              <p className="text-sm text-gray-400 mb-3">
                Pedime ajustes en lenguaje natural antes de renderizar.
              </p>
              <div className="flex flex-col gap-1.5 items-center">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="text-xs text-[#0C447C] bg-blue-50 border border-blue-200 rounded-full px-3 py-1.5 hover:bg-blue-100"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap
                ${m.role === "user"
                  ? "bg-[#185FA5] text-white rounded-br-sm"
                  : "bg-gray-100 text-gray-800 rounded-bl-sm"}`}>
                {m.text}
              </div>
            </div>
          ))}

          {sending && (
            <div className="flex justify-start">
              <div className="bg-gray-100 text-gray-400 rounded-2xl rounded-bl-sm px-3.5 py-2 text-sm">
                Pensando...
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mx-5 mb-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <div className="flex gap-2 px-5 py-4 border-t border-gray-100">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ej: bajá la música y subí el volumen de la voz"
            rows={1}
            className="flex-1 input-field text-sm resize-none"
            disabled={!configured}
          />
          <button
            onClick={() => send()}
            disabled={sending || !input.trim() || !configured}
            className={`px-4 rounded-lg border text-sm cursor-pointer transition-colors
              ${input.trim() && !sending && configured
                ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
                : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
          >
            Enviar
          </button>
        </div>
      </div>
    </div>
  )
}
