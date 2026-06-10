import { useState } from "react"

const MATCHES = [
  "España vs Marruecos", "Argentina vs Francia", "Brasil vs Alemania",
  "Portugal vs Uruguay", "España vs Argentina", "México vs Ecuador",
]

export default function NewProjectModal({ onCreated, onClose }) {
  const [form, setForm] = useState({
    title: "", topic: "", match: "", match_date: "",
    aspect: "9:16", language: "es",
  })
  const [loading, setLoading] = useState(false)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async () => {
    if (!form.title || !form.topic) return
    setLoading(true)
    const res = await fetch("/api/projects/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    })
    const project = await res.json()
    setLoading(false)
    onCreated(project)
  }

  return (
    <div style={{
      position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
    }}>
      <div style={{
        background: "var(--color-background-primary, #fff)",
        borderRadius: 14, padding: 28, width: 460, maxWidth: "90vw",
        border: "0.5px solid var(--color-border-tertiary, #e0e0e0)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 20 }}>
          <h2 style={{ margin: 0, fontSize: 17, fontWeight: 500 }}>Nuevo vídeo</h2>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "#999" }}>✕</button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={labelStyle}>Título del vídeo</label>
            <input value={form.title} onChange={e => set("title", e.target.value)}
              placeholder="España vs Marruecos — Previa táctica"
              style={inputStyle} />
          </div>

          <div>
            <label style={labelStyle}>Tema / Prompt para el guión</label>
            <textarea value={form.topic} onChange={e => set("topic", e.target.value)}
              placeholder="Analiza las fortalezas y debilidades de ambas selecciones antes del partido, con estadísticas clave del Mundial 2026"
              style={{ ...inputStyle, height: 80, resize: "vertical" }} />
          </div>

          <div>
            <label style={labelStyle}>Partido (opcional)</label>
            <select value={form.match} onChange={e => set("match", e.target.value)} style={inputStyle}>
              <option value="">— Sin partido específico —</option>
              {MATCHES.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>

          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Fecha del partido</label>
              <input type="date" value={form.match_date} onChange={e => set("match_date", e.target.value)} style={inputStyle} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Formato</label>
              <select value={form.aspect} onChange={e => set("aspect", e.target.value)} style={inputStyle}>
                <option value="9:16">9:16 Vertical (YouTube Shorts / TikTok)</option>
                <option value="16:9">16:9 Horizontal (YouTube)</option>
              </select>
            </div>
          </div>

          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Idioma</label>
              <select value={form.language} onChange={e => set("language", e.target.value)} style={inputStyle}>
                <option value="es">Español</option>
                <option value="pt">Portugués</option>
                <option value="en">Inglés</option>
              </select>
            </div>
          </div>

          <button onClick={handleSubmit} disabled={loading || !form.title || !form.topic} style={{
            marginTop: 6, padding: "9px 0", borderRadius: 8,
            background: form.title && form.topic ? "#185FA5" : "#ccc",
            color: form.title && form.topic ? "#E6F1FB" : "#888",
            border: "none", cursor: form.title && form.topic ? "pointer" : "not-allowed",
            fontSize: 14, fontWeight: 500,
          }}>
            {loading ? "Creando..." : "Crear proyecto"}
          </button>
        </div>
      </div>
    </div>
  )
}

const labelStyle = { display: "block", fontSize: 12, color: "#888", marginBottom: 4 }
const inputStyle = {
  width: "100%", padding: "7px 10px", borderRadius: 7,
  border: "0.5px solid #ccc", fontSize: 13, boxSizing: "border-box",
  background: "var(--color-background-primary, #fff)",
  color: "var(--color-text-primary, #1a1a1a)",
}
