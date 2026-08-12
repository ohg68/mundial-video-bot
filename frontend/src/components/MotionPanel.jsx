import { useState, useEffect } from "react"

const DEFAULTS = {
  intro: { title: "", subtitle: "", badge: "LayerCut", accentColor: "#f5c518" },
  outro: { headline: "¿Te gustó el video?", cta: "SUSCRÍBETE", handle: "", accentColor: "#f5c518" },
  captions: { accentColor: "#f5c518" },
}

const FIELDS = {
  intro: [
    { key: "title", label: "Título", placeholder: "Ej: GOLES ÉPICOS" },
    { key: "subtitle", label: "Subtítulo", placeholder: "Ej: Semifinal Brasil vs Francia" },
    { key: "badge", label: "Badge", placeholder: "Ej: Mundial 2026" },
  ],
  outro: [
    { key: "headline", label: "Mensaje", placeholder: "Ej: ¿Te gustó el video?" },
    { key: "cta", label: "Botón CTA", placeholder: "Ej: SUSCRÍBETE" },
    { key: "handle", label: "Usuario / canal", placeholder: "Ej: @micanal" },
  ],
  captions: [],
}

export default function MotionPanel({ projectId, projectTitle, onClose }) {
  const [tab, setTab] = useState("intro")
  const [vars, setVars] = useState({
    intro: { ...DEFAULTS.intro, title: projectTitle || "" },
    outro: { ...DEFAULTS.outro },
    captions: { ...DEFAULTS.captions },
  })
  const [status, setStatus] = useState({ intro: { exists: false }, outro: { exists: false }, captions: { exists: false } })
  const [configured, setConfigured] = useState(true)
  const [soloConfigurado, setSoloConfigurado] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)
  const [previewKey, setPreviewKey] = useState(0)

  const refreshStatus = () => {
    fetch(`/api/motion/${projectId}`)
      .then(r => r.json())
      .then(d => { if (d.intro) setStatus(d) })
      .catch(() => {})
  }

  useEffect(() => {
    fetch("/api/motion/status")
      .then(r => r.json())
      // `available` y no `configured`: la variable puede estar puesta y el
      // servicio no existir, que es justo lo que pasaba.
      .then(d => { setConfigured(!!d.available); setSoloConfigurado(!!d.configured) })
      .catch(() => {})
    refreshStatus()
  }, [projectId])

  const setField = (key, value) =>
    setVars(v => ({ ...v, [tab]: { ...v[tab], [key]: value } }))

  const handleGenerate = async () => {
    setGenerating(true)
    setError(null)
    try {
      const res = await fetch(`/api/motion/${projectId}/${tab}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variables: vars[tab] }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || `Error ${res.status}`)
      } else {
        refreshStatus()
        setPreviewKey(k => k + 1)
      }
    } catch {
      setError("Error de conexión al generar")
    }
    setGenerating(false)
  }

  const handleDelete = async () => {
    await fetch(`/api/motion/${projectId}/${tab}`, { method: "DELETE" })
    refreshStatus()
  }

  const current = status[tab] || { exists: false }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl p-5 sm:p-6 w-full sm:w-[640px] sm:max-w-[95vw] border border-gray-200 max-h-[90dvh] flex flex-col overflow-y-auto">
        <div className="flex justify-between items-center mb-4">
          <h2 className="m-0 text-base font-medium">Intro / Outro animados</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        {!configured && (
          <div className="mb-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {soloConfigurado
              ? "El servicio de motion graphics no responde. HYPERFRAMES_URL apunta a un servicio que no está levantado."
              : "El servicio de motion graphics no está configurado (HYPERFRAMES_URL)."}
          </div>
        )}

        {/* Tabs intro/outro/captions */}
        <div className="flex gap-2 mb-4">
          {["intro", "outro", "captions"].map(k => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-3.5 py-1.5 rounded-lg text-xs border cursor-pointer transition-colors capitalize
                ${tab === k
                  ? "bg-blue-50 border-blue-300 text-[#0C447C] font-medium"
                  : "bg-transparent border-gray-200 text-gray-500 hover:bg-gray-50"}`}
            >
              {k === "intro" ? "🎬 Intro" : k === "outro" ? "👋 Outro" : "💬 Captions"}
              {status[k]?.exists && <span className="ml-1.5 text-green-600">●</span>}
            </button>
          ))}
        </div>

        {tab === "captions" && (
          <div className="mb-3 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
            💬 Subtítulos kinéticos estilo Reels: cada palabra se anima al ritmo de la voz.
            Requiere haber generado la narración y los subtítulos. En el render full
            sustituyen a los subtítulos clásicos.
          </div>
        )}

        {/* Campos */}
        <div className="flex flex-col gap-3 mb-3">
          {FIELDS[tab].map(f => (
            <div key={f.key}>
              <label className="block text-xs text-gray-400 mb-1">{f.label}</label>
              <input
                value={vars[tab][f.key] || ""}
                onChange={e => setField(f.key, e.target.value)}
                placeholder={f.placeholder}
                className="input-field"
              />
            </div>
          ))}
          <div>
            <label className="block text-xs text-gray-400 mb-1">Color de acento</label>
            <input
              type="color"
              value={vars[tab].accentColor}
              onChange={e => setField("accentColor", e.target.value)}
              className="w-16 h-9 rounded cursor-pointer border border-gray-200"
            />
          </div>
        </div>

        {error && (
          <div className="mb-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* Preview */}
        {current.exists && (
          <div className="mb-3">
            <div className="text-[11px] text-gray-400 font-medium mb-1.5">Vista previa</div>
            <video
              key={`${tab}-${previewKey}`}
              src={`/api/motion/${projectId}/${tab}/preview?v=${previewKey}`}
              controls
              muted
              className="w-full max-h-[300px] rounded-lg bg-black"
            />
          </div>
        )}

        {/* Acciones */}
        <div className="flex justify-between items-center gap-2 mt-auto">
          {current.exists ? (
            <button onClick={handleDelete} className="btn-outline text-red-500 border-red-200 hover:bg-red-50">
              🗑 Eliminar {tab}
            </button>
          ) : <span />}
          <div className="flex gap-2">
            <button onClick={onClose} className="btn-outline">Cerrar</button>
            <button
              onClick={handleGenerate}
              disabled={generating || !configured}
              className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
                ${!generating && configured
                  ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
                  : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
            >
              {generating ? "⏳ Generando (~30s)..." : `⚡ Generar ${tab}`}
            </button>
          </div>
        </div>

        <p className="text-[11px] text-gray-400 mt-3 mb-0">
          La intro y la outro se añaden automáticamente al video en el render full.
        </p>
      </div>
    </div>
  )
}
