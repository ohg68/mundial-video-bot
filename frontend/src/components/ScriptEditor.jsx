import { useState } from "react"

export default function ScriptEditor({ projectId, script, onClose, onSaved }) {
  const [text, setText] = useState(script || "")
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleGenerate = async () => {
    setGenerating(true)
    const res = await fetch(`/api/layers/${projectId}/generate/script`, { method: "POST" })
    const data = await res.json()
    setText(data.script || "")
    setGenerating(false)
  }

  const handleSave = async () => {
    setSaving(true)
    await fetch(`/api/layers/${projectId}/script`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script: text }),
    })
    setSaving(false)
    onSaved(text)
    onClose()
  }

  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0
  const estSeconds = Math.round(wordCount / 2.5)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl p-5 sm:p-6 w-full sm:w-[600px] sm:max-w-[92vw] border border-gray-200 max-h-[90dvh] flex flex-col">
        <div className="flex justify-between items-center mb-4">
          <h2 className="m-0 text-base font-medium">Guión del vídeo</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        <div className="flex gap-2 mb-3">
          <button onClick={handleGenerate} disabled={generating} className="btn-outline">
            {generating ? "⏳ Generando con IA..." : "⚡ Generar con IA"}
          </button>
        </div>

        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          className="w-full h-[280px] sm:h-[320px] p-3 rounded-lg border border-gray-200 text-sm leading-relaxed resize-y bg-gray-50 text-gray-900 font-sans focus:outline-none focus:border-[#0C447C] transition-colors"
          placeholder="El guión aparecerá aquí. Puedes editarlo libremente antes de generar el audio..."
        />

        <div className="flex justify-between items-center mt-3">
          <span className="text-xs text-gray-400">
            {wordCount} palabras · ~{estSeconds} segundos
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
