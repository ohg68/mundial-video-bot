import { useState, useEffect } from "react"

const LLM_PROVIDERS = [
  { key: "deepseek", label: "DeepSeek" },
  { key: "claude", label: "Claude" },
  { key: "openai", label: "GPT" },
]

export default function ScriptEditor({ projectId, script, topic, match, matchDate, onClose, onSaved }) {
  const [text, setText] = useState(script || "")
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [provider, setProvider] = useState("deepseek")
  const [template, setTemplate] = useState("free")
  const [templates, setTemplates] = useState({})
  const [timestamps, setTimestamps] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch("/api/sources/script/templates")
      .then(r => r.json())
      .then(d => setTemplates(d.templates || {}))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!text.trim()) { setTimestamps([]); return }
    const timer = setTimeout(() => {
      fetch("/api/sources/script/timestamps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: text }),
      })
        .then(r => r.json())
        .then(d => setTimestamps(d.timestamps || []))
        .catch(() => {})
    }, 500)
    return () => clearTimeout(timer)
  }, [text])

  const handleGenerate = async () => {
    setGenerating(true)
    setError(null)
    try {
      const res = await fetch("/api/sources/script/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic || "video del Mundial 2026", provider, template, match, match_date: matchDate }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || `Error ${res.status}`)
      } else {
        setText(data.script || "")
        if (data.timestamps) setTimestamps(data.timestamps)
      }
    } catch (e) {
      setError("Error de conexión al generar el guión")
    }
    setGenerating(false)
  }

  const handleSave = async () => {
    setSaving(true)
    await fetch(`/api/layers/${projectId}/script`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script: text }),
    })
    // Save LLM + template preferences
    await fetch(`/api/layers/${projectId}/config/llm`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_provider: provider, script_template: template }),
    })
    setSaving(false)
    onSaved(text)
    onClose()
  }

  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0
  const totalSeconds = timestamps.length > 0
    ? timestamps[timestamps.length - 1].end
    : Math.round(wordCount / 2.5)

  const formatTime = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl p-5 sm:p-6 w-full sm:w-[700px] sm:max-w-[95vw] border border-gray-200 max-h-[90dvh] flex flex-col">
        <div className="flex justify-between items-center mb-4">
          <h2 className="m-0 text-base font-medium">Guión del vídeo</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        {/* LLM Provider selector */}
        <div className="mb-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] text-gray-400">Modelo:</span>
            {LLM_PROVIDERS.map(p => (
              <button
                key={p.key}
                onClick={() => setProvider(p.key)}
                className={`px-2.5 py-1 rounded-md text-xs border cursor-pointer transition-colors
                  ${provider === p.key
                    ? "bg-blue-50 border-blue-300 text-[#0C447C] font-medium"
                    : "bg-transparent border-gray-200 text-gray-500 hover:bg-gray-50"}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Template selector */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] text-gray-400">Plantilla:</span>
            {Object.entries(templates).map(([key, tmpl]) => (
              <button
                key={key}
                onClick={() => setTemplate(key)}
                className={`px-2.5 py-1 rounded-md text-xs border cursor-pointer transition-colors
                  ${template === key
                    ? "bg-amber-50 border-amber-300 text-amber-800 font-medium"
                    : "bg-transparent border-gray-200 text-gray-500 hover:bg-gray-50"}`}
                title={tmpl.description}
              >
                {tmpl.name}
              </button>
            ))}
          </div>
        </div>

        {/* Generate button */}
        <div className="flex gap-2 mb-3">
          <button onClick={handleGenerate} disabled={generating} className="btn-outline">
            {generating ? "⏳ Generando..." : "⚡ Generar con IA"}
          </button>
        </div>

        {error && (
          <div className="mb-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* Editor + Timestamps side by side on desktop */}
        <div className="flex gap-3 flex-1 min-h-0">
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            className="flex-1 min-h-[250px] sm:min-h-[300px] p-3 rounded-lg border border-gray-200 text-sm leading-relaxed resize-y bg-gray-50 text-gray-900 font-sans focus:outline-none focus:border-[#0C447C] transition-colors"
            placeholder="El guión aparecerá aquí. Puedes editarlo libremente antes de generar el audio..."
          />

          {/* Timestamps panel */}
          {timestamps.length > 0 && (
            <div className="hidden sm:block w-[180px] shrink-0 overflow-y-auto">
              <div className="text-[11px] text-gray-400 font-medium mb-1.5">Timestamps</div>
              <div className="space-y-1">
                {timestamps.map((block, i) => (
                  <div
                    key={i}
                    className="text-[11px] p-1.5 rounded bg-gray-50 border border-gray-100"
                  >
                    <div className="flex justify-between text-gray-400 mb-0.5">
                      <span>{formatTime(block.start)}</span>
                      <span>{block.duration}s</span>
                    </div>
                    <div className="text-gray-600 line-clamp-2 leading-tight">
                      {block.text.slice(0, 60)}...
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between items-center mt-3 flex-wrap gap-2">
          <span className="text-xs text-gray-400">
            {wordCount} palabras · ~{formatTime(totalSeconds)} · {timestamps.length} bloques
          </span>
          <div className="flex gap-2">
            <button onClick={onClose} className="btn-outline">Cancelar</button>
            <button
              onClick={handleSave}
              disabled={saving || !text}
              className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
                ${text
                  ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
                  : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
            >
              {saving ? "Guardando..." : "Guardar guión"}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
