import { useState, useEffect } from "react"
import LayerCard from "./LayerCard"
import ScriptEditor from "./ScriptEditor"
import VideoPreview from "./VideoPreview"
import Timeline from "./Timeline"
import RenderHistory from "./RenderHistory"
import PublishPanel from "./PublishPanel"
import EditingPanel from "./EditingPanel"
import MotionPanel from "./MotionPanel"
import AssistantPanel from "./AssistantPanel"
import useProjectSocket from "../hooks/useProjectSocket"
import { apiJson } from "../api"

const LAYERS = [
  { key: "video",     label: "Vídeo",            color: "#0C447C", bg: "#E6F1FB", icon: "🎬" },
  { key: "audio",     label: "Narración",         color: "#27500A", bg: "#EAF3DE", icon: "🎙" },
  { key: "music",     label: "Música de fondo",   color: "#633806", bg: "#FAEEDA", icon: "🎵" },
  { key: "subtitles", label: "Subtítulos",         color: "#3C3489", bg: "#EEEDFE", icon: "💬" },
  { key: "overlay",   label: "Overlay / branding", color: "#712B13", bg: "#FAECE7", icon: "🏷" },
]

// Necesarias para que el vídeo tenga sentido; el resto son añadidos. Se muestran
// por separado para que "sin configurar" en música no se lea como algo que falta.
const REQUIRED_KEYS = ["video", "audio", "subtitles"]
const REQUIRED_LAYERS = LAYERS.filter(l => REQUIRED_KEYS.includes(l.key))
const OPTIONAL_LAYERS = LAYERS.filter(l => !REQUIRED_KEYS.includes(l.key))

export default function ProjectEditor({ project: initialProject, onRefresh, onMenuOpen, mobileTab }) {
  const [project, setProject] = useState(initialProject)
  const [rendering, setRendering] = useState(false)
  const [outputUrl, setOutputUrl] = useState(null)
  const [showScript, setShowScript] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [showPublish, setShowPublish] = useState(false)
  const [showEditing, setShowEditing] = useState(false)
  const [showMotion, setShowMotion] = useState(false)
  const [showAssistant, setShowAssistant] = useState(false)
  const [assistantReviewMode, setAssistantReviewMode] = useState(false)
  const [layerDurations, setLayerDurations] = useState(null)

  const { connected, lastEvent, progress, taskType, isRunning, isDone, isFailed } = useProjectSocket(project.id)

  useEffect(() => {
    setProject(initialProject)
    setOutputUrl(null)
  }, [initialProject?.id])

  const fetchDurations = async () => {
    const data = await apiJson(`/api/render/${project.id}/durations`)
    if (data.layers) {
      setLayerDurations(data.layers)
      // Si ya existe un render (de esta sesión o de una anterior), mostrar
      // la tarjeta de revisión — no depende de haber apretado "Render" ahora.
      if (data.layers.output?.exists) {
        setOutputUrl(`/api/render/${project.id}/download`)
      }
    }
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

  const handleLayerUpdate = () => {
    apiJson(`/api/projects/${project.id}`).then(d => { if (d.id) setProject(d) })
    fetchDurations()
  }

  const handleClearRenders = async () => {
    if (!confirm("¿Limpiar todos los renders de este proyecto?")) return
    await apiJson(`/api/projects/${project.id}/renders`, { method: "DELETE" })
    setOutputUrl(null)
  }

  const readyCount = Object.values(project.layers || {}).filter(s => s === "ready").length
  const requiredReady = REQUIRED_KEYS.filter(k => project.layers?.[k] === "ready").length
  const canRender = readyCount >= 2

  // El render queda "viejo" si alguna capa se regeneró después del final.mp4.
  // Margen de 2s para no marcarlo por el desfase natural del propio render.
  const outputInfo = layerDurations?.output
  const isStale = !!outputInfo?.exists && LAYERS.some(l => {
    const info = layerDurations?.[l.key]
    return info?.exists && (info.mtime || 0) > (outputInfo.mtime || 0) + 2
  })

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
    <div className="p-4 md:p-7 max-w-[1200px]">
      {/* Header */}
      <div className="flex items-start justify-between mb-4 gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button onClick={onMenuOpen}
              className="md:hidden bg-transparent border-none cursor-pointer text-lg p-0 text-gray-500">☰</button>
            <h1 className="text-lg md:text-xl font-semibold tracking-tight m-0 truncate">{project.title}</h1>
            {connected && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" title="Conectado en vivo" />}
          </div>
          <div className="text-[13px] text-gray-400 flex gap-3 mt-1 flex-wrap">
            {project.match && <span>⚽ {project.match}</span>}
            {project.match_date && <span>📅 {project.match_date}</span>}
            <span>{project.config?.aspect || "9:16"}</span>
          </div>
        </div>
        <button
          onClick={() => { setAssistantReviewMode(false); setShowAssistant(true) }}
          className="btn-primary shrink-0 whitespace-nowrap"
        >
          🤖 Asistente IA
        </button>
      </div>

      {/* Toolbar secundario */}
      <div className="flex gap-1.5 items-center flex-wrap mb-4 pb-4 border-b border-gray-100">
        <button onClick={() => setShowScript(true)} className="btn-action">✏️ Guión</button>
        <button onClick={() => setShowEditing(true)} className="btn-action">🎬 Edición</button>
        <button onClick={() => setShowMotion(true)} className="btn-action">✨ Intro/Outro</button>
        <span className="w-px h-4 bg-gray-200 mx-1" />
        <button onClick={() => setShowHistory(true)} className="btn-action">📂 Historial</button>
        <button onClick={() => setShowPublish(true)} className="btn-action">📤 Publicar</button>
        <button onClick={handleClearRenders} className="btn-action text-red-500 hover:bg-red-50 hover:border-red-200">
          🗑 Renders
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

      {/* Dos columnas: lo que configurás (capas) | lo que obtenés (resultado).
          En móvil se apilan con el resultado arriba — ahí lo normal es mirar,
          no configurar. */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,400px)] items-start">

        {/* Capas */}
        <div className="order-2 lg:order-1">
          <div className="text-[11px] text-gray-400 mb-1.5">
            Capas · {requiredReady} de {REQUIRED_KEYS.length} necesarias
          </div>
          {REQUIRED_LAYERS.map(layer => (
            <LayerCard
              key={layer.key}
              projectId={project.id}
              layer={layer}
              status={project.layers?.[layer.key] || "empty"}
              config={project.config?.[layer.key] || {}}
              layerInfo={project.layer_info?.[layer.key]}
              script={project.config?.script}
              onUpdate={handleLayerUpdate}
            />
          ))}

          <div className="text-[11px] text-gray-400 mt-4 mb-1.5">Opcionales</div>
          {OPTIONAL_LAYERS.map(layer => (
            <LayerCard
              key={layer.key}
              projectId={project.id}
              layer={layer}
              status={project.layers?.[layer.key] || "empty"}
              config={project.config?.[layer.key] || {}}
              layerInfo={project.layer_info?.[layer.key]}
              script={project.config?.script}
              onUpdate={handleLayerUpdate}
              optional
            />
          ))}

          <div className="mt-4">
            <VideoPreview projectId={project.id} layers={layerDurations} showOutput={false} />
            <Timeline layers={layerDurations} />
          </div>
        </div>

        {/* Resultado */}
        <div className="order-1 lg:order-2 lg:sticky lg:top-4">
          <div className="text-[11px] text-gray-400 mb-1.5">Resultado</div>

          {outputUrl ? (
            <div className={`rounded-xl overflow-hidden border ${isStale ? "border-amber-300" : "border-gray-200"} bg-white`}>
              <video
                key={outputUrl}
                src={outputUrl}
                controls
                className="w-full max-h-[380px] bg-black block"
              />

              {isStale && (
                <div className="bg-amber-50 border-t border-amber-200 px-3 py-2 text-[12px] text-amber-800 flex gap-1.5">
                  <span aria-hidden="true">⚠️</span>
                  <span>Cambiaste una capa después de renderizar. Este resultado está viejo.</span>
                </div>
              )}

              <div className="p-2.5">
                <button
                  onClick={() => handleRender("full")}
                  disabled={rendering || !canRender}
                  className={`w-full py-2 rounded-lg text-[13px] font-medium cursor-pointer transition-colors mb-1.5 border
                    ${!canRender || rendering
                      ? "bg-gray-100 text-gray-400 border-gray-100 cursor-not-allowed"
                      : isStale
                        ? "bg-amber-500 text-white border-amber-500 hover:bg-amber-600"
                        : "bg-[#185FA5] text-white border-[#185FA5] hover:bg-[#0C447C]"}`}
                >
                  {rendering ? "⏳ Renderizando..." : isStale ? "🔄 Volver a renderizar" : "▶ Renderizar de nuevo"}
                </button>

                <div className="flex gap-1.5 mb-1.5">
                  <a href={outputUrl} download title="Descargar"
                    className="flex-1 text-center py-1.5 rounded-lg border border-gray-200 text-[13px] no-underline text-gray-600 hover:bg-gray-50">⬇</a>
                  <button onClick={() => setShowPublish(true)} title="Publicar"
                    className="flex-1 py-1.5 rounded-lg border border-gray-200 bg-white text-[13px] cursor-pointer text-gray-600 hover:bg-gray-50">📤</button>
                  <button onClick={() => setShowHistory(true)} title="Historial"
                    className="flex-1 py-1.5 rounded-lg border border-gray-200 bg-white text-[13px] cursor-pointer text-gray-600 hover:bg-gray-50">📂</button>
                </div>

                <button
                  onClick={() => { setAssistantReviewMode(true); setShowAssistant(true) }}
                  className="w-full py-1.5 rounded-lg border border-gray-200 bg-white text-[12px] cursor-pointer text-gray-600 hover:bg-gray-50"
                >
                  💬 Pedir cambios
                </button>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center">
              <div className="text-2xl mb-2">🎬</div>
              <p className="text-[13px] text-gray-500 m-0">Todavía no renderizaste</p>
              <p className="text-[11px] text-gray-400 mt-1 mb-3">
                {canRender ? "Ya podés generar el vídeo final." : "Generá al menos el vídeo y la narración."}
              </p>
              <button
                onClick={() => handleRender("full")}
                disabled={rendering || !canRender}
                className={`w-full py-2 rounded-lg text-[13px] font-medium cursor-pointer transition-colors border
                  ${canRender && !rendering
                    ? "bg-[#185FA5] text-white border-[#185FA5] hover:bg-[#0C447C]"
                    : "bg-gray-100 text-gray-400 border-gray-100 cursor-not-allowed"}`}
              >
                {rendering ? "⏳ Renderizando..." : "▶ Renderizar"}
              </button>
            </div>
          )}

          <button
            onClick={() => handleRender("quick")}
            disabled={rendering || !canRender}
            className={`w-full mt-1.5 py-1.5 rounded-lg border text-[12px] cursor-pointer transition-colors
              ${canRender && !rendering
                ? "bg-white text-amber-800 border-amber-200 hover:bg-amber-50"
                : "bg-gray-50 text-gray-400 border-gray-100 cursor-not-allowed"}`}
          >
            ⚡ Vista previa rápida (540p)
          </button>
        </div>
      </div>

      {showScript && (
        <ScriptEditor
          projectId={project.id}
          script={project.config?.script}
          topic={project.topic || project.title}
          match={project.match}
          matchDate={project.match_date}
          targetSeconds={project.config?.target_seconds}
          llmProvider={project.config?.llm_provider}
          scriptTemplate={project.config?.script_template}
          onClose={() => setShowScript(false)}
          onSaved={(script, prefs) => setProject(p => ({
            ...p, config: { ...p.config, script, ...(prefs || {}) }
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

      {showEditing && (
        <EditingPanel
          projectId={project.id}
          config={project.config}
          onClose={() => setShowEditing(false)}
          onSaved={() => {
            apiJson(`/api/projects/${project.id}`).then(d => { if (d.id) setProject(d) })
          }}
        />
      )}

      {showMotion && (
        <MotionPanel
          projectId={project.id}
          projectTitle={project.title}
          onClose={() => setShowMotion(false)}
        />
      )}

      {showAssistant && (
        <AssistantPanel
          projectId={project.id}
          reviewMode={assistantReviewMode}
          onClose={() => setShowAssistant(false)}
          onUpdate={() => {
            apiJson(`/api/projects/${project.id}`).then(d => { if (d.id) setProject(d) })
            fetchDurations()
          }}
        />
      )}
    </div>
  )
}
