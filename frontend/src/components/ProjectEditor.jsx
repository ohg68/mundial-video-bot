import { useState, useEffect } from "react"
import LayerCard from "./LayerCard"
import ScriptEditor from "./ScriptEditor"
import VideoPreview from "./VideoPreview"
import Timeline from "./Timeline"
import RenderHistory from "./RenderHistory"
import PublishPanel from "./PublishPanel"
import useProjectSocket from "../hooks/useProjectSocket"
import { apiJson } from "../api"

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
  const [showScript, setShowScript] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [showPublish, setShowPublish] = useState(false)
  const [layerDurations, setLayerDurations] = useState(null)

  const { connected, lastEvent, progress, taskType, isRunning, isDone, isFailed } = useProjectSocket(project.id)

  useEffect(() => {
    setProject(initialProject)
    setOutputUrl(null)
  }, [initialProject?.id])

  const fetchDurations = async () => {
    const data = await apiJson(`/api/render/${project.id}/durations`)
    if (data.layers) setLayerDurations(data.layers)
  }

  useEffect(() => { fetchDurations() }, [project.id])

  useEffect(() => {
    if (isDone || isFailed) {
      apiJson(`/api/projects/${project.id}`).then(data => {
        if (data.id) setProject(data)
      })
      fetchDurations()
    }
  }, [isDone, isFailed])

  useEffect(() => {
    if (connected) return
    const interval = setInterval(async () => {
      const data = await apiJson(`/api/projects/${project.id}`)
      if (data.id) setProject(data)
    }, 3000)
    return () => clearInterval(interval)
  }, [project.id, connected])

  const handleRender = async (quality = "full") => {
    setRendering(true)
    const endpoint = quality === "quick"
      ? `/api/render/${project.id}/quick`
      : `/api/render/${project.id}`
    await apiJson(endpoint, { method: "POST" })
    setTimeout(async () => {
      setOutputUrl(`/api/render/${project.id}/download`)
      setRendering(false)
    }, 5000)
  }

  const handleClearRenders = async () => {
    if (!confirm("¿Limpiar todos los renders de este proyecto?")) return
    await apiJson(`/api/projects/${project.id}/renders`, { method: "DELETE" })
    setOutputUrl(null)
  }

  const readyCount = Object.values(project.layers || {}).filter(s => s === "ready").length

  // Mobile preview tab
  if (mobileTab === "preview") {
    return (
      <div className="p-4 md:hidden">
        <h2 className="text-lg font-medium mb-3">{project.title}</h2>
        <VideoPreview projectId={project.id} layers={layerDurations} />
        <Timeline layers={layerDurations} />
      </div>
    )
  }

  return (
    <div className="p-4 md:p-7 max-w-[760px]">
      {/* Header */}
      <div className="flex items-start justify-between mb-5 gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button onClick={onMenuOpen}
              className="md:hidden bg-transparent border-none cursor-pointer text-lg p-0 text-gray-500">☰</button>
            <h1 className="text-lg md:text-xl font-medium m-0 truncate">{project.title}</h1>
            {connected && <span className="w-2 h-2 rounded-full bg-green-400 shrink-0" title="WebSocket conectado" />}
          </div>
          <div className="text-[13px] text-gray-400 flex gap-3 mt-1 flex-wrap">
            {project.match && <span>⚽ {project.match}</span>}
            {project.match_date && <span>📅 {project.match_date}</span>}
            <span>{project.config?.aspect || "9:16"}</span>
          </div>
        </div>
        <div className="flex gap-2 items-center shrink-0 flex-wrap justify-end">
          <button onClick={() => setShowScript(true)} className="btn-outline">✏️ Guión</button>
          <button onClick={() => setShowHistory(true)} className="btn-outline">📂 Historial</button>
          <button onClick={() => setShowPublish(true)} className="btn-outline">📤 Publicar</button>
          <button onClick={handleClearRenders} className="btn-outline text-red-500 border-red-200 hover:bg-red-50">
            🗑 Renders
          </button>
        </div>
      </div>

      {/* Render buttons */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <button
          onClick={() => handleRender("quick")}
          disabled={rendering || readyCount < 2}
          className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
            ${readyCount >= 2
              ? "bg-amber-50 text-amber-800 border-amber-300 hover:bg-amber-100"
              : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
        >
          {rendering ? "⏳" : "⚡"} Preview 540p
        </button>
        <button
          onClick={() => handleRender("full")}
          disabled={rendering || readyCount < 2}
          className={`px-3.5 py-1.5 rounded-lg border text-[13px] cursor-pointer transition-colors
            ${readyCount >= 2
              ? "bg-[#185FA5] text-blue-100 border-[#185FA5] hover:bg-[#0C447C]"
              : "bg-gray-200 text-gray-500 border-gray-200 cursor-not-allowed"}`}
        >
          {rendering ? "⏳ Renderizando..." : "▶ Render full"}
        </button>
      </div>

      {/* WebSocket progress bar */}
      {isRunning && progress !== null && (
        <div className="mb-4 rounded-lg bg-blue-50 border border-blue-200 p-3">
          <div className="flex justify-between text-xs text-blue-800 mb-1.5">
            <span>{taskType === "render" ? "Renderizando" : `Generando ${taskType}`}</span>
            <span>{progress}%</span>
          </div>
          <div className="w-full h-2 bg-blue-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-[#0C447C] rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {isFailed && lastEvent?.error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 p-3 text-xs text-red-700">
          Error: {lastEvent.error}
        </div>
      )}

      {/* Video preview + Timeline */}
      <VideoPreview projectId={project.id} layers={layerDurations} />
      <Timeline layers={layerDurations} />

      {/* Output card */}
      {outputUrl && (
        <div className="bg-green-50 border border-green-400 rounded-xl px-4 py-3 mb-3 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="font-medium text-sm text-green-900">✅ Vídeo listo</div>
            <div className="text-xs text-green-700">{project.title}</div>
          </div>
          <div className="flex gap-2">
            <a href={outputUrl} download className="btn-outline no-underline text-[13px]">⬇ Descargar</a>
            <button onClick={() => setShowPublish(true)} className="btn-outline">
              📤 Publicar
            </button>
          </div>
        </div>
      )}

      <div className="mb-1.5 text-[13px] text-gray-400">{readyCount}/5 capas listas</div>

      {LAYERS.map(layer => (
        <LayerCard
          key={layer.key}
          projectId={project.id}
          layer={layer}
          status={project.layers?.[layer.key] || "empty"}
          config={project.config?.[layer.key] || {}}
          layerInfo={project.layer_info?.[layer.key]}
          script={project.config?.script}
          onUpdate={() => {
            apiJson(`/api/projects/${project.id}`).then(d => { if (d.id) setProject(d) })
            fetchDurations()
          }}
        />
      ))}

      {showScript && (
        <ScriptEditor
          projectId={project.id}
          script={project.config?.script}
          topic={project.topic || project.title}
          match={project.match}
          matchDate={project.match_date}
          onClose={() => setShowScript(false)}
          onSaved={(script) => setProject(p => ({
            ...p, config: { ...p.config, script }
          }))}
        />
      )}

      {showHistory && (
        <RenderHistory
          projectId={project.id}
          onClose={() => setShowHistory(false)}
        />
      )}

      {showPublish && (
        <PublishPanel
          projectId={project.id}
          title={project.title}
          onClose={() => setShowPublish(false)}
        />
      )}
    </div>
  )
}
