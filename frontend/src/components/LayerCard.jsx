import { useState, useRef } from "react"

const STATUS_LABEL = {
  empty: "Sin configurar",
  pending: "Generando...",
  ready: "Lista",
  error: "Error",
}
const STATUS_COLOR = {
  empty: "#aaa", pending: "#EF9F27", ready: "#639922", error: "#E24B4A"
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
    <div style={{
      background: "var(--color-background-primary, #fff)",
      border: "0.5px solid var(--color-border-tertiary, #e0e0e0)",
      borderRadius: 12, padding: "14px 16px", marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: expanded ? 12 : 0 }}>
        <span style={{
          fontSize: 11, fontWeight: 500, padding: "3px 10px",
          borderRadius: 6, background: layer.bg, color: layer.color,
        }}>
          {layer.icon} {layer.label}
        </span>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            background: STATUS_COLOR[status], display: "inline-block",
          }} />
          <span style={{ fontSize: 12, color: "#888" }}>
            {loading ? "Procesando..." : STATUS_LABEL[status]}
            {layerInfo?.clips && ` · ${layerInfo.clips} clips`}
            {layerInfo?.voice && ` · ${layerInfo.voice}`}
          </span>
        </div>
        <button onClick={() => setExpanded(!expanded)} style={{
          background: "none", border: "none", cursor: "pointer",
          fontSize: 12, color: "#999", padding: "2px 6px",
        }}>
          {expanded ? "▲" : "▼"}
        </button>
      </div>

      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {canGenerate && (
              <button onClick={handleGenerate} disabled={loading} style={actionBtn}>
                {loading ? "⏳" : "⚡"} Generar automático
              </button>
            )}
            <button onClick={() => fileRef.current.click()} disabled={loading} style={actionBtn}>
              ⬆ Reemplazar con mi archivo
            </button>
            <input
              ref={fileRef}
              type="file"
              style={{ display: "none" }}
              accept={
                layer.key === "video" ? "video/*"
                : layer.key === "audio" || layer.key === "music" ? "audio/*"
                : layer.key === "subtitles" ? ".srt,.vtt"
                : "image/*"
              }
              onChange={handleReplace}
            />
            {status === "ready" && (
              <button onClick={handleDownload} style={actionBtn}>
                ⬇ Descargar capa
              </button>
            )}
          </div>

          {volume !== null && (
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 12, color: "#888", minWidth: 60 }}>Volumen</span>
              <input
                type="range" min={0} max={100} step={1}
                value={Math.round(volume)}
                onChange={e => handleVolumeChange(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: 12, color: "#888", minWidth: 32 }}>
                {Math.round(volume)}%
              </span>
            </div>
          )}

          {layer.key === "video" && (
            <div style={{ display: "flex", gap: 8 }}>
              {["local", "pexels", "mixed"].map(src => (
                <button key={src} onClick={async () => {
                  await fetch(`/api/layers/${projectId}/config/video`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ source: src }),
                  })
                }} style={{
                  ...actionBtn,
                  background: config.source === src ? "#E6F1FB" : "transparent",
                  borderColor: config.source === src ? "#378ADD" : "#ccc",
                  color: config.source === src ? "#0C447C" : "inherit",
                }}>
                  {src === "local" ? "📁 Mis vídeos" : src === "pexels" ? "🌐 Pexels" : "🔀 Mixto"}
                </button>
              ))}
            </div>
          )}

          {layer.key === "audio" && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {["es-ES-AlvaroNeural", "es-ES-ElviraNeural", "pt-PT-DuarteNeural"].map(v => (
                <button key={v} onClick={async () => {
                  await fetch(`/api/layers/${projectId}/config/audio`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ voice: v }),
                  })
                }} style={{
                  ...actionBtn,
                  background: config.voice === v ? "#EAF3DE" : "transparent",
                  borderColor: config.voice === v ? "#639922" : "#ccc",
                  color: config.voice === v ? "#27500A" : "inherit",
                }}>
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

const actionBtn = {
  padding: "5px 12px", borderRadius: 7, border: "0.5px solid #ccc",
  background: "transparent", cursor: "pointer", fontSize: 12,
}
