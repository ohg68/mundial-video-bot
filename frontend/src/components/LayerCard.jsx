import { useState, useRef } from "react"

const STATUS_LABEL = {
  empty: "Sin configurar",
  pending: "Generando...",
  ready: "Lista",
  error: "Error",
}
const STATUS_DOT = {
  empty: "bg-gray-400", pending: "bg-amber-400", ready: "bg-green-500", error: "bg-red-500"
}

export default function LayerCard({ projectId, layer, status, config, layerInfo, onUpdate }) {
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [volume, setVolume] = useState(
    layer.key === "audio" ? (config.volume || 0.9) * 100
    : layer.key === "music" ? (config.volume || 0.25) * 100
    : null
  )
  const fileRef = useRef()

  const handleGenerate = async () => {
    setLoading(true)
    const layerKey = layer.key === "audio" ? "audio" : layer.key === "video" ? "video" : layer.key
    await fetch(`/api/layers/${projectId}/generate/${layerKey}`, { method: "POST" })
    setLoading(false)
    onUpdate()
  }

  const handleReplace = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setLoading(true)
    const form = new FormData()
    form.append("file", file)
    await fetch(`/api/layers/${projectId}/replace/${layer.key}`, {
      method: "POST", body: form,
    })
    setLoading(false)
    onUpdate()
  }

  const handleDownload = () => {
    window.open(`/api/layers/${projectId}/download/${layer.key}`, "_blank")
  }

  const handleVolumeChange = async (val) => {
    setVolume(val)
    await fetch(`/api/layers/${projectId}/config/${layer.key}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume: val / 100 }),
    })
  }

  const canGenerate = ["audio", "video", "subtitles"].includes(layer.key)

  return (
    <div className="bg-white border border-gray-200 rounded-xl mb-2.5 overflow-hidden">
      {/* Header — tap to expand */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2.5 px-4 py-3 bg-transparent border-none cursor-pointer text-left"
      >
        <span
          className="text-[11px] font-medium px-2.5 py-0.5 rounded-md"
          style={{ background: layer.bg, color: layer.color }}
        >
          {layer.icon} {layer.label}
        </span>
        <div className="flex-1 flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${STATUS_DOT[status]}`} />
          <span className="text-xs text-gray-400">
            {loading ? "Procesando..." : STATUS_LABEL[status]}
            {layerInfo?.clips && ` · ${layerInfo.clips} clips`}
            {layerInfo?.voice && ` · ${layerInfo.voice}`}
          </span>
        </div>
        <span className="text-xs text-gray-400">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="flex flex-col gap-2.5 px-4 pb-3 pt-0">
          <div className="flex gap-2 flex-wrap">
            {canGenerate && (
              <button onClick={handleGenerate} disabled={loading} className="btn-action">
                {loading ? "⏳" : "⚡"} Generar automático
              </button>
            )}
            <button onClick={() => fileRef.current.click()} disabled={loading} className="btn-action">
              ⬆ Reemplazar con mi archivo
            </button>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              accept={
                layer.key === "video" ? "video/*"
                : layer.key === "audio" || layer.key === "music" ? "audio/*"
                : layer.key === "subtitles" ? ".srt,.vtt"
                : "image/*"
              }
              onChange={handleReplace}
            />
            {status === "ready" && (
              <button onClick={handleDownload} className="btn-action">
                ⬇ Descargar capa
              </button>
            )}
          </div>

          {volume !== null && (
            <div className="flex items-center gap-2.5">
              <span className="text-xs text-gray-400 min-w-[60px]">Volumen</span>
              <input
                type="range" min={0} max={100} step={1}
                value={Math.round(volume)}
                onChange={e => handleVolumeChange(Number(e.target.value))}
                className="flex-1 accent-[#0C447C]"
              />
              <span className="text-xs text-gray-400 min-w-[32px]">
                {Math.round(volume)}%
              </span>
            </div>
          )}

          {layer.key === "video" && (
            <div className="flex gap-2">
              {["local", "pexels", "mixed"].map(src => (
                <button key={src} onClick={async () => {
                  await fetch(`/api/layers/${projectId}/config/video`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ source: src }),
                  })
                }} className={`btn-action ${config.source === src
                  ? "bg-blue-50 border-blue-400 text-[#0C447C]"
                  : ""}`}
                >
                  {src === "local" ? "📁 Mis vídeos" : src === "pexels" ? "🌐 Pexels" : "🔀 Mixto"}
                </button>
              ))}
            </div>
          )}

          {layer.key === "audio" && (
            <div className="flex gap-2 flex-wrap">
              {["es-ES-AlvaroNeural", "es-ES-ElviraNeural", "pt-PT-DuarteNeural"].map(v => (
                <button key={v} onClick={async () => {
                  await fetch(`/api/layers/${projectId}/config/audio`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ voice: v }),
                  })
                }} className={`btn-action ${config.voice === v
                  ? "bg-green-50 border-green-400 text-green-900"
                  : ""}`}
                >
                  {v.replace("Neural", "").replace("-", " ")}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
