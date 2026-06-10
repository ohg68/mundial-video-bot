import { useState, useEffect } from "react"
import LayerCard from "./LayerCard"
import ScriptEditor from "./ScriptEditor"

const LAYERS = [
  { key: "video",     label: "Vídeo",           color: "#0C447C", bg: "#E6F1FB", icon: "🎬" },
  { key: "audio",     label: "Narración",        color: "#27500A", bg: "#EAF3DE", icon: "🎙" },
  { key: "music",     label: "Música de fondo",  color: "#633806", bg: "#FAEEDA", icon: "🎵" },
  { key: "subtitles", label: "Subtítulos",        color: "#3C3489", bg: "#EEEDFE", icon: "💬" },
  { key: "overlay",   label: "Overlay / branding",color: "#712B13", bg: "#FAECE7", icon: "🏷" },
]

export default function ProjectEditor({ project: initialProject, onRefresh }) {
  const [project, setProject] = useState(initialProject)
  const [rendering, setRendering] = useState(false)
  const [outputUrl, setOutputUrl] = useState(null)
  const [publishing, setPublishing] = useState(false)
  const [showScript, setShowScript] = useState(false)

  useEffect(() => {
    setProject(initialProject)
    setOutputUrl(null)
  }, [initialProject?.id])

  useEffect(() => {
    const interval = setInterval(async () => {
      const res = await fetch(`/api/projects/${project.id}`)
      const data = await res.json()
      setProject(data)
    }, 3000)
    return () => clearInterval(interval)
  }, [project.id])

  const handleRender = async () => {
    setRendering(true)
    await fetch(`/api/render/${project.id}`, { method: "POST" })
    setTimeout(async () => {
      setOutputUrl(`/api/render/${project.id}/download`)
      setRendering(false)
    }, 5000)
  }

  const handlePublishYoutube = async () => {
    setPublishing(true)
    const res = await fetch(`/api/publish/${project.id}/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: project.title }),
    })
    const data = await res.json()
    setPublishing(false)
    if (data.url) alert(`Publicado: ${data.url}`)
    else alert(data.message || "Error al publicar")
  }

  const readyCount = Object.values(project.layers || {}).filter(s => s === "ready").length

  return (
    <div style={{ padding: 28, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 500 }}>{project.title}</h1>
          <div style={{ fontSize: 13, color: "#888", display: "flex", gap: 12 }}>
            {project.match && <span>⚽ {project.match}</span>}
            {project.match_date && <span>📅 {project.match_date}</span>}
            <span>{project.config?.aspect || "9:16"}</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={() => setShowScript(true)} style={btnStyle}>
            ✏️ Guión
          </button>
          <button onClick={handleRender} disabled={rendering || readyCount < 2} style={{
            ...btnStyle,
            background: readyCount >= 2 ? "#185FA5" : "#ccc",
            color: readyCount >= 2 ? "#E6F1FB" : "#888",
            borderColor: readyCount >= 2 ? "#185FA5" : "#ccc",
          }}>
            {rendering ? "⏳ Renderizando..." : "▶ Render final"}
          </button>
        </div>
      </div>

      {outputUrl && (
        <div style={{
          background: "#EAF3DE", border: "0.5px solid #639922",
          borderRadius: 10, padding: "12px 16px", marginBottom: 20,
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <div>
            <div style={{ fontWeight: 500, fontSize: 14, color: "#27500A" }}>✅ Vídeo listo</div>
            <div style={{ fontSize: 12, color: "#3B6D11" }}>{project.title}</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <a href={outputUrl} download style={{ ...btnStyle, textDecoration: "none", fontSize: 13 }}>
              ⬇ Descargar
            </a>
            <button onClick={handlePublishYoutube} disabled={publishing} style={btnStyle}>
              {publishing ? "Publicando..." : "▶ YouTube"}
            </button>
          </div>
        </div>
      )}

      <div style={{ marginBottom: 6, fontSize: 13, color: "#888" }}>
        {readyCount}/5 capas listas
      </div>

      {LAYERS.map(layer => (
        <LayerCard
          key={layer.key}
          projectId={project.id}
          layer={layer}
          status={project.layers?.[layer.key] || "empty"}
          config={project.config?.[layer.key] || {}}
          layerInfo={project.layer_info?.[layer.key]}
          script={project.config?.script}
          onUpdate={() => {}}
        />
      ))}

      {showScript && (
        <ScriptEditor
          projectId={project.id}
          script={project.config?.script}
          onClose={() => setShowScript(false)}
          onSaved={(script) => setProject(p => ({
            ...p, config: { ...p.config, script }
          }))}
        />
      )}
    </div>
  )
}

const btnStyle = {
  padding: "6px 14px", borderRadius: 8,
  border: "0.5px solid #ccc", background: "transparent",
  cursor: "pointer", fontSize: 13,
}
