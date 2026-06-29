import { useState } from "react"

const CATEGORIES = [
  "Marketing / Promoción",
  "Educación / Tutorial",
  "Noticias / Actualidad",
  "Entretenimiento",
  "Corporativo / Empresa",
  "Producto / Demo",
  "Redes sociales",
  "Otro",
]

export default function NewProjectModal({ onCreated, onClose }) {
  const [form, setForm] = useState({
    title: "", topic: "", category: "", date: "",
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

  const canSubmit = form.title && form.topic

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl p-5 sm:p-7 w-full sm:w-[480px] sm:max-w-[90vw] border border-gray-200 max-h-[90dvh] overflow-y-auto">
        <div className="flex justify-between items-center mb-5">
          <h2 className="m-0 text-base sm:text-lg font-medium">Nuevo proyecto</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        <div className="flex flex-col gap-3.5">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Título del vídeo</label>
            <input
              value={form.title}
              onChange={e => set("title", e.target.value)}
              placeholder="Ej: Cómo usar RFID en hoteles, Tutorial de Odoo..."
              className="input-field"
            />
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-1">Tema / Prompt para el guión</label>
            <textarea
              value={form.topic}
              onChange={e => set("topic", e.target.value)}
              placeholder="Describe de qué trata el vídeo. Cuanto más detalle, mejor será el guión generado."
              className="input-field h-[90px] resize-y"
            />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">Categoría</label>
              <select value={form.category} onChange={e => set("category", e.target.value)} className="input-field">
                <option value="">— Seleccionar —</option>
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">Fecha (opcional)</label>
              <input type="date" value={form.date} onChange={e => set("date", e.target.value)} className="input-field" />
            </div>
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">Formato</label>
              <select value={form.aspect} onChange={e => set("aspect", e.target.value)} className="input-field">
                <option value="9:16">9:16 Vertical (Shorts / TikTok)</option>
                <option value="16:9">16:9 Horizontal (YouTube)</option>
                <option value="1:1">1:1 Cuadrado (Instagram)</option>
              </select>
            </div>
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">Idioma del guión</label>
              <select value={form.language} onChange={e => set("language", e.target.value)} className="input-field">
                <option value="es">Español</option>
                <option value="pt">Portugués</option>
                <option value="en">English</option>
                <option value="it">Italiano</option>
                <option value="fr">Français</option>
                <option value="de">Deutsch</option>
              </select>
            </div>
          </div>

          <button
            onClick={handleSubmit}
            disabled={loading || !canSubmit}
            className={`mt-1.5 py-2.5 rounded-lg border-none text-sm font-medium transition-colors
              ${canSubmit
                ? "bg-[#185FA5] text-blue-100 cursor-pointer hover:bg-[#0C447C]"
                : "bg-gray-200 text-gray-500 cursor-not-allowed"}`}
          >
            {loading ? "Creando..." : "Crear proyecto"}
          </button>
        </div>
      </div>
    </div>
  )
}
