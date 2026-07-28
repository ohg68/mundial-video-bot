import { useState, useEffect } from "react"

const VIDEO_TYPES = [
  { key: "marketing", label: "Marketing", icon: "📊" },
  { key: "music", label: "Musical", icon: "🎵" },
  { key: "sports", label: "Deportes", icon: "⚽" },
]

const PLACEHOLDERS = {
  marketing: "Ej: Ritmo constante, cortes cada 2-3 segundos, zoom suave en producto...",
  music: "Ej: Sincronizar cortes al beat, build-up lento, clímax en el drop...",
  sports: "Ej: Cortes rápidos en jugadas, slow-motion en goles, energía alta...",
}

const LLM_PROVIDERS = [
  { key: "deepseek", label: "DeepSeek" },
  { key: "claude", label: "Claude" },
  { key: "openai", label: "GPT" },
]

export default function EditingPanel({ projectId, config, onClose, onSaved }) {
  const editing = config?.editing || {}
  const [videoType, setVideoType] = useState(editing.video_type || "marketing")
  const [bpm, setBpm] = useState(editing.bpm || 120)
  const [prompt, setPrompt] = useState(editing.editing_prompt || "")
  const [provider, setProvider] = useState("deepseek")
  const [plan, setPlan] = useState(editing.editing_plan || null)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`/api/editing/${projectId}/plan`)
      .then(r => r.json())
      .then(d => {
        if (d.plan) setPlan(d.plan)
        if (d.video_type) setVideoType(d.video_type)
        if (d.editing_prompt) setPrompt(d.editing_prompt)
        if (d.bpm) setBpm(d.bpm)
      })
      .catch(() => {})
  }, [projectId])

  const handleGenerate = async () => {
    if (!prompt.trim()) return
    setGenerating(true)
    setError(null)
    try {
      const res = await fetch(`/api/editing/${projectId}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_type: videoType,
          editing_prompt: prompt,
          bpm: videoType === "music" ? bpm : null,
          provider,
          total_duration: 90,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || `Error ${res.status}`)
      } else {
        setPlan(data.plan)
      }
    } catch (e) {
      setError("Error de conexión al generar el plan")
    }
    setGenerating(false)
  }

  const handleSave = async () => {
    if (!plan) return
    setSaving(true)
    await fetch(`/api/editing/${projectId}/plan`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan, video_type: videoType, editing_prompt: prompt,
        bpm: videoType === "music" ? bpm : null,
      }),
    })
    setSaving(false)
    onSaved?.()
    onClose()
  }

  const maxDuration = plan?.total_duration || 90
  const segments = plan?.segments || []

  const transitionColor = (t) => {
    if (t === "xfade") return "bg-purple-400"
    if (t === "fade") return "bg-blue-400"
    return "bg-gray-300"
  }

  const intensityOpacity = (i) => 0.3 + i * 0.7

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl p-5 sm:p-6 w-full sm:w-[700px] sm:max-w-[95vw] border border-gray-200 max-h-[90dvh] flex flex-col">
        <div className="flex justify-between items-center mb-4">
          <h2 className="m-0 text-base font-medium">Ritmo de edición</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600 p-1">✕</button>
        </div>

        {/* Video type pills */}
        <div className="flex gap-2 mb-3">
          {VIDEO_TYPES.map(t => (
            <button
              key={t.key}
              onClick={() => setVideoType(t.key)}
              className={`px-3 py-1.5 rounded-lg text-xs border cursor-pointer transition-colors flex items-center gap-1.5
                ${videoType === t.key
                  ? "bg-blue-50 border-blue-300 text-[#0C447C] font-medium"
                  : "bg-transparent border-gray-200 text-gray-500 hover:bg-gray-50"}`}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        {/* BPM input for music */}
        {videoType === "music" && (
          <div className="flex items-center gap-2 mb-3">
            <label className="text-xs text-gray-400">BPM:</label>
            <input
              type="number"
              value={bpm}
              onChange={e => setBpm(parseInt(e.target.value) || 120)}
              min={60} max={200}
              className="input-field w-20 text-center"
            />
            <span className="text-[11px] text-gray-400">({(60 / bpm).toFixed(2)}s/beat)</span>
          </div>
        )}

        {/* LLM provider */}
        <div className="flex items-center gap-2 mb-3">
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

        {/* Editing prompt */}
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder={PLACEHOLDERS[videoType]}
          className="input-field h-[80px] resize-y mb-3 text-sm"
        />

        {/* Generate button */}
        <button
          onClick={handleGenerate}
          disabled={generating || !prompt.trim()}
          className="btn-outline mb-3 self-start"
        >
          {generating ? "⏳ Generando plan..." : "⚡ Generar plan con IA"}
        </button>

        {error && (
          <div className="mb-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* Plan preview timeline */}
        {plan && segments.length > 0 && (
          <div className="flex-1 min-h-0 overflow-y-auto">
            <div className="text-[11px] text-gray-400 font-medium mb-2">
              Plan: {segments.length} segmentos · {maxDuration}s · {plan.pacing_curve}
            </div>

            {/* Visual timeline bar */}
            <div className="flex h-8 rounded-lg overflow-hidden border border-gray-200 mb-3">
              {segments.map((seg, i) => {
                const width = ((seg.end - seg.start) / maxDuration) * 100
                return (
                  <div
                    key={i}
                    className="relative group"
                    style={{
                      width: `${width}%`,
                      backgroundColor: `rgba(12, 68, 124, ${intensityOpacity(seg.intensity || 0.5)})`,
                      borderRight: i < segments.length - 1 ? "1px solid white" : "none",
                    }}
                    title={`${seg.start}s–${seg.end}s | ${seg.transition} | ${seg.effect || "none"}`}
                  >
                    {width > 5 && (
                      <span className="absolute inset-0 flex items-center justify-center text-[9px] text-white/80">
                        {(seg.end - seg.start).toFixed(1)}s
                      </span>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Segment list */}
            <div className="space-y-1 max-h-[200px] overflow-y-auto">
              {segments.map((seg, i) => (
                <div key={i} className="flex items-center gap-2 text-[11px] p-1.5 rounded bg-gray-50 border border-gray-100">
                  <span className="text-gray-400 w-6 text-right">{i + 1}</span>
                  <span className="text-gray-600 w-24">{seg.start}s – {seg.end}s</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] text-white ${transitionColor(seg.transition)}`}>
                    {seg.transition}
                  </span>
                  {seg.effect && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-100 text-amber-700">
                      {seg.effect}
                    </span>
                  )}
                  <span className="text-gray-300 ml-auto">int: {(seg.intensity || 0).toFixed(1)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="flex justify-end gap-2 mt-3">
          <button onClick={onClose} className="btn-outline">Cancelar</button>
          <button
            onClick={handleSave}
            disabled={saving || !plan}
            className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
              ${plan
                ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
                : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
          >
            {saving ? "Guardando..." : "Guardar plan"}
          </button>
        </div>
      </div>
    </div>
  )
}
