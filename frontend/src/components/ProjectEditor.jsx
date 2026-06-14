import { useState, useEffect } from "react"
import LayerCard from "./LayerCard"
import ScriptEditor from "./ScriptEditor"

const LAYERS = [
  { key: "video",     label: "Vídeo",            color: "#0C447C", bg: "#E6F1FB", icon: "🎬" },
  { key: "audio",     label: "Narración",         color: "#27500A", bg: "#EAF3DE", icon: "🎙" },
  { key: "music",     label: "Música de fondo",   color: "#633806", bg: "#FAEEDA", icon: "🎵" },
  { key: "subtitles", label: "Subtítulos",         color: "#3C3489", bg: "#EEEDFE", icon: "💬" },
  { key: "overlay",   label: "Overlay / branding", color: "#712B13", bg: "#FAECE7", icon: "🏷" },
]

export default function ProjectEditor({ project: initialProject, onRefresh, onMenuOpen, mobileTab }) {
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

  const handleClearRenders = async () => {
    if (!confirm("¿Limpiar todos los renders de este proyecto?")) return
    await fetch(`/api/projects/${project.id}/renders`, { method: "DELETE" })
    setOutputUrl(null)
  }

  const readyCount = Object.values(project.layers || {}).filter(s => s === "ready").length

  // Mobile preview tab
  if (mobileTab === "preview") {
    return (
      <div className="p-4 md:hidden">
        <h2 className="text-lg font-medium mb-3">{project.title}</h2>
        {outputUrl ? (
          <div className="space-y-3">
            <video
              src={outputUrl}
              controls
              className="w-full rounded-xl bg-black"
              playsInline
            />
            <div className="flex gap-2">
              <a href={outputUrl} download className="flex-1 text-center py-2.5 rounded-lg bg-[#0C447C] text-white text-sm no-underline">
                ⬇ Descargar
              </a>
              <button
                onClick={handlePublishYoutube}
                disabled={publishing}
                className="flex-1 py-2.5 rounded-lg border border-gray-300 bg-white text-sm cursor-pointer"
              >
                {publishing ? "Publicando..." : "▶ YouTube"}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-16 text-gray-400">
            <span className="text-4xl mb-2">🎬</span>
            <p className="text-sm">Aún no hay render disponible</p>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="p-4 md:p-7 max-w-[760px]">
      {/* Header */}
      <div className="flex items-start justify-between mb-5 gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button
              onClick={onMenuOpen}
              className="md:hidden bg-transparent border-none cursor-pointer text-lg p-0 text-gray-500"
            >
              ☰
            </button>
            <h1 className="text-lg md:text-xl font-medium m-0 truncate">{project.title}</h1>
          </div>
          <div className="text-[13px] text-gray-400 flex gap-3 mt-1 flex-wrap">
            {project.match && <span>⚽ {project.match}</span>}
            {project.match_date && <span>📅 {project.match_date}</span>}
            <span>{project.config?.aspect || "9:16"}</span>
          </div>
        </div>
        <div className="flex gap-2 items-center shrink-0 flex-wrap justify-end">
          <button onClick={() => setShowScript(true)} className="btn-outline">
            ✏️ Guión
          </button>
          <button onClick={handleClearRenders} className="btn-outline text-red-500 border-red-200 hover:bg-red-50">
            🗑 Limpiar renders
          </button>
          <button
            onClick={handleRender}
            disabled={rendering || readyCount < 2}
            className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
              ${readyCount >= 2
                ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
                : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
          >
            {rendering ? "⏳ Renderizando..." : "▶ Render final"}
          </button>
        </div>
      </div>

      {/* Output card */}
      {outputUrl && (
        <div className="bg-green-50 border border-green-400 rounded-xl px-4 py-3 mb-5 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="font-medium text-sm text-green-900">✅ Vídeo listo</div>
            <div className="text-xs text-green-700">{project.title}</div>
          </div>
          <div className="flex gap-2">
            <a href={outputUrl} download className="btn-outline no-underline text-[13px]">
              ⬇ Descargar
            </a>
            <button onClick={handlePublishYoutube} disabled={publishing} className="btn-outline">
              {publishing ? "Publicando..." : "▶ YouTube"}
            </button>
          </div>
        </div>
      )}

      <div className="mb-1.5 text-[13px] text-gray-400">
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
