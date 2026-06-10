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
    <div style={{
      position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
    }}>
      <div style={{
        background: "var(--color-background-primary, #fff)",
        borderRadius: 14, padding: 24, width: 600, maxWidth: "92vw",
        border: "0.5px solid #e0e0e0",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 500 }}>Guión del vídeo</h2>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "#999" }}>✕</button>
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button onClick={handleGenerate} disabled={generating} style={btn}>
            {generating ? "⏳ Generando con Claude..." : "⚡ Generar con Claude"}
          </button>
        </div>

        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          style={{
            width: "100%", height: 280, padding: 12, borderRadius: 8,
            border: "0.5px solid #ccc", fontSize: 14, lineHeight: 1.6,
            resize: "vertical", boxSizing: "border-box",
            fontFamily: "var(--font-sans, system-ui)",
            background: "var(--color-background-secondary, #f9f9f7)",
            color: "var(--color-text-primary, #1a1a1a)",
          }}
          placeholder="El guión aparecerá aquí. Puedes editarlo libremente antes de generar el audio..."
        />

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
          <span style={{ fontSize: 12, color: "#999" }}>
            {wordCount} palabras · ~{estSeconds} segundos
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} style={btn}>Cancelar</button>
            <button onClick={handleSave} disabled={saving || !text} style={{
              ...btn,
              background: text ? "#185FA5" : "#ccc",
              color: text ? "#E6F1FB" : "#888",
              borderColor: text ? "#185FA5" : "#ccc",
            }}>
              {saving ? "Guardando..." : "Guardar guión"}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

const btn = {
  padding: "6px 14px", borderRadius: 7, border: "0.5px solid #ccc",
  background: "transparent", cursor: "pointer", fontSize: 13,
}
