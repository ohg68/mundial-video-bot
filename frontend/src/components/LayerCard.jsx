import { useState, useRef, useEffect } from "react"
import ClipPicker from "./ClipPicker"
import MediaLibrary from "./MediaLibrary"

const STATUS_LABEL = {
  empty: "Sin configurar",
  pending: "Generando...",
  ready: "Lista",
  error: "Error",
}
const STATUS_DOT = {
  empty: "bg-gray-400", pending: "bg-amber-400", ready: "bg-green-500", error: "bg-red-500"
}

const TTS_PROVIDERS = [
  { key: "edge", label: "Edge TTS", desc: "Gratis" },
  { key: "openai", label: "OpenAI TTS", desc: "HD" },
  { key: "elevenlabs", label: "ElevenLabs", desc: "Clonación" },
]

const OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

// Las fuentes que el pipeline sabe montar, las mismas que ofrece el bot. Antes
// esta lista incluía "YouTube CC", que no tiene rama en el ensamblado (elegirlo
// no daba error: simplemente no hacía lo que prometía), y le faltaban tres que
// sí funcionan.
const VIDEO_SOURCES = [
  { key: "local", label: "📁 Mis vídeos" },
  { key: "pexels", label: "🌐 Pexels" },
  { key: "pixabay", label: "🟢 Pixabay" },
  { key: "coverr", label: "🎥 Coverr" },
  { key: "stock", label: "🎞 Máxima variedad" },
  { key: "mixed", label: "🔀 Mixto" },
  { key: "photos", label: "🖼 Fotos de Internet" },
  { key: "wikimedia", label: "🏛 Wikimedia (libre)" },
  { key: "mixed_photos", label: "🎬 Mix (Fotos + Video)" },
  { key: "gdrive", label: "📁 Google Drive" },
]

export default function LayerCard({ projectId, layer, status, config, layerInfo, onUpdate, optional = false }) {
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [volume, setVolume] = useState(
    layer.key === "audio" ? (config.volume || 0.9) * 100
    : layer.key === "music" ? (config.volume || 0.25) * 100
    : null
  )
  const [showClipPicker, setShowClipPicker] = useState(false)
  const [showMediaLibrary, setShowMediaLibrary] = useState(false)
  const [previewingVoice, setPreviewingVoice] = useState(false)
  const [previewAudio, setPreviewAudio] = useState(null)
  const [removingBg, setRemovingBg] = useState(false)
  const [bgError, setBgError] = useState(null)
  const [edgeVoices, setEdgeVoices] = useState([])
  const [configError, setConfigError] = useState(null)
  const fileRef = useRef()
  const audioRef = useRef()

  // Las voces vienen del backend. Antes había tres escritas a mano acá dentro,
  // mientras este endpoint ya servía muchas más y nadie lo llamaba.
  useEffect(() => {
    if (layer.key !== "audio") return
    let vivo = true
    fetch("/api/sources/tts/voices")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => { if (vivo) setEdgeVoices(d.edge || []) })
      .catch(() => { if (vivo) setEdgeVoices([]) })
    return () => { vivo = false }
  }, [layer.key])

  // La voz efectiva es voice_name: es la que el pipeline usa cuando existe, y la
  // única que puede nombrar voces fuera del enum (es-AR, it-IT...).
  const voiceActual = config.voice_name || config.voice || "es-ES-AlvaroNeural"

  const handleGenerate = async () => {
    setLoading(true)
    const layerKey = layer.key === "audio" ? "audio" : layer.key === "video" ? "video" : layer.key
    await fetch(`/api/layers/${projectId}/generate/${layerKey}`, { method: "POST" })
    setLoading(false)
    onUpdate()
  }

  const handleReplace = async (e) => {
    const files = Array.from(e.target.files)
    if (!files.length) return
    setLoading(true)

    if (files.length > 1 && layer.key === "video") {
      const form = new FormData()
      files.forEach(f => form.append("files", f))
      await fetch(`/api/sources/${projectId}/clips/upload`, { method: "POST", body: form })
    } else {
      const form = new FormData()
      form.append("file", files[0])
      await fetch(`/api/layers/${projectId}/replace/${layer.key}`, { method: "POST", body: form })
    }
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

  const handleVoicePreview = async () => {
    setPreviewingVoice(true)
    const provider = config.tts_provider || "edge"
    const body = {
      provider,
      voice: provider === "openai" ? (config.openai_voice || "onyx") : voiceActual,
      voice_id: config.elevenlabs_voice_id,
      speed: config.speed || 1.0,
    }
    try {
      const res = await fetch("/api/sources/tts/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        setPreviewAudio(url)
        if (audioRef.current) {
          audioRef.current.src = url
          audioRef.current.play()
        }
      }
    } catch (e) {}
    setPreviewingVoice(false)
  }

  const updateConfig = async (updates) => {
    setConfigError(null)
    try {
      const res = await fetch(`/api/layers/${projectId}/config/${layer.key}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      })
      if (!res.ok) {
        // Antes esto se lanzaba y nadie miraba la respuesta: un fallo al guardar
        // se veía igual que un guardado correcto.
        const data = await res.json().catch(() => ({}))
        setConfigError(data.detail || `No se pudo guardar (error ${res.status})`)
        return
      }
      onUpdate()
    } catch (e) {
      setConfigError("No se pudo guardar: sin conexión con el servidor")
    }
  }

  const handleRemoveBg = async () => {
    setRemovingBg(true)
    setBgError(null)
    try {
      const res = await fetch(`/api/layers/${projectId}/overlay/remove-bg`, { method: "POST" })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setBgError(data.detail || `Error ${res.status}`)
      } else {
        onUpdate()
      }
    } catch (e) {
      setBgError("Error de conexión")
    }
    setRemovingBg(false)
  }

  const canGenerate = ["audio", "video", "subtitles"].includes(layer.key)

  return (
    <div className={`rounded-xl mb-2.5 overflow-hidden ${
      optional && status === "empty"
        ? "bg-transparent border border-dashed border-gray-300"
        : "bg-white border border-gray-200"
    }`}>
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
          {/* Una capa opcional vacía no está "sin configurar": simplemente no la
              usaste. Se ofrece añadirla en vez de marcarla como pendiente. */}
          {optional && status === "empty" && !loading ? (
            <span className="text-xs text-[#185FA5]">Añadir</span>
          ) : (
            <>
              <span className={`w-2 h-2 rounded-full ${STATUS_DOT[status]}`} />
              <span className="text-xs text-gray-400">
                {loading ? "Procesando..." : STATUS_LABEL[status]}
                {layerInfo?.clips && ` · ${layerInfo.clips} clips`}
                {layerInfo?.voice && ` · ${layerInfo.voice}`}
                {layerInfo?.provider && ` · ${layerInfo.provider}`}
              </span>
            </>
          )}
        </div>
        <span className="text-xs text-gray-400">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="flex flex-col gap-2.5 px-4 pb-3 pt-0">
          {/* Action buttons */}
          <div className="flex gap-2 flex-wrap">
            {canGenerate && (
              <button onClick={handleGenerate} disabled={loading} className="btn-action">
                {loading ? "⏳" : "⚡"} Generar automático
              </button>
            )}
            <button onClick={() => fileRef.current.click()} disabled={loading} className="btn-action">
              ⬆ {layer.key === "video" ? "Subir clip(s)" : "Reemplazar archivo"}
            </button>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              multiple={layer.key === "video"}
              accept={
                layer.key === "video" ? "video/*"
                : layer.key === "audio" || layer.key === "music" ? "audio/*"
                : layer.key === "subtitles" ? ".srt,.vtt"
                : "image/*"
              }
              onChange={handleReplace}
            />
            {layer.key === "video" && (
              <button onClick={() => setShowClipPicker(true)} className="btn-action">
                🔍 Buscar clips
              </button>
            )}
            {layer.key === "video" && (
              <button onClick={() => setShowMediaLibrary(true)} className="btn-action">
                📚 Biblioteca
              </button>
            )}
            {status === "ready" && (
              <button onClick={handleDownload} className="btn-action">
                ⬇ Descargar capa
              </button>
            )}
            {layer.key === "overlay" && status === "ready" && (
              <button onClick={handleRemoveBg} disabled={removingBg} className="btn-action">
                {removingBg ? "⏳ Quitando fondo..." : "🪄 Quitar fondo"}
              </button>
            )}
          </div>

          {bgError && (
            <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {bgError}
            </div>
          )}

          {/* Volume slider */}
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

          {/* Video source selector */}
          {layer.key === "video" && (
            <div className="space-y-2">
              <div className="flex gap-1.5 flex-wrap">
                {VIDEO_SOURCES.map(src => (
                  <button
                    key={src.key}
                    onClick={() => updateConfig({ source: src.key })}
                    className={`btn-action ${config.source === src.key
                      ? "bg-blue-50 border-blue-400 text-[#0C447C]" : ""}`}
                  >
                    {src.label}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-2 text-[11px] text-gray-500 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.ab_split !== false}
                  onChange={e => updateConfig({ ab_split: e.target.checked })}
                  className="w-3.5 h-3.5 accent-[#0C447C]"
                />
                🎯 Ajustar imágenes al guion (recomendado — si no, elige tomas genéricas al azar)
              </label>
            </div>
          )}

          {/* Audio: TTS provider + voice + preview */}
          {layer.key === "audio" && (
            <div className="space-y-2.5">
              {/* TTS Provider */}
              <div>
                <span className="text-[11px] text-gray-400 block mb-1">Proveedor TTS</span>
                <div className="flex gap-1.5 flex-wrap">
                  {TTS_PROVIDERS.map(p => (
                    <button
                      key={p.key}
                      onClick={() => updateConfig({ tts_provider: p.key })}
                      className={`btn-action ${(config.tts_provider || "edge") === p.key
                        ? "bg-green-50 border-green-400 text-green-900" : ""}`}
                    >
                      {p.label} <span className="text-[10px] text-gray-400 ml-1">{p.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Voice selector based on provider */}
              <div>
                <span className="text-[11px] text-gray-400 block mb-1">Voz</span>

                {(!config.tts_provider || config.tts_provider === "edge") && (
                  edgeVoices.length === 0
                    ? <span className="text-[11px] text-gray-400">Cargando voces...</span>
                    : (
                      <div className="space-y-2">
                        {Object.entries(
                          edgeVoices.reduce((grupos, v) => {
                            const k = v.region || v.lang
                            ;(grupos[k] = grupos[k] || []).push(v)
                            return grupos
                          }, {})
                        ).map(([region, voces]) => (
                          <div key={region}>
                            <span className="text-[10px] text-gray-400 block mb-1">{region}</span>
                            <div className="flex gap-1.5 flex-wrap">
                              {voces.map(v => (
                                // Se escribe voice_name, no voice: es el campo que el
                                // pipeline respeta y el único que admite voces que no
                                // están en el enum.
                                <button key={v.id} onClick={() => updateConfig({ voice_name: v.id })}
                                  className={`btn-action ${voiceActual === v.id
                                    ? "bg-green-50 border-green-400 text-green-900" : ""}`}>
                                  {v.name}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )
                )}

                <div className="flex gap-1.5 flex-wrap">
                  {config.tts_provider === "openai" && (
                    OPENAI_VOICES.map(v => (
                      <button key={v} onClick={() => updateConfig({ openai_voice: v })}
                        className={`btn-action capitalize ${config.openai_voice === v ? "bg-green-50 border-green-400 text-green-900" : ""}`}>
                        {v}
                      </button>
                    ))
                  )}
                  {config.tts_provider === "elevenlabs" && (
                    <input
                      type="text"
                      placeholder="Voice ID de ElevenLabs"
                      defaultValue={config.elevenlabs_voice_id || ""}
                      // Al salir del campo y no en cada tecla: así no se manda un
                      // PATCH por carácter ni se recarga el proyecto mientras escribís.
                      onBlur={e => {
                        const val = e.target.value.trim()
                        if (val !== (config.elevenlabs_voice_id || "")) {
                          updateConfig({ elevenlabs_voice_id: val })
                        }
                      }}
                      className="input-field text-xs w-56"
                    />
                  )}
                </div>

                {configError && (
                  <span className="text-[11px] text-red-600 block mt-1.5">{configError}</span>
                )}
              </div>

              {/* Voice preview */}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleVoicePreview}
                  disabled={previewingVoice}
                  className="btn-action"
                >
                  {previewingVoice ? "⏳ Generando..." : "🔊 Preview de voz"}
                </button>
                <audio ref={audioRef} className="hidden" />
                {previewAudio && (
                  <span className="text-[11px] text-green-600">✓ Reproduciendo</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {showClipPicker && (
        <ClipPicker
          projectId={projectId}
          onClose={() => setShowClipPicker(false)}
          onClipsSelected={(clips) => { onUpdate() }}
        />
      )}

      {showMediaLibrary && (
        <MediaLibrary
          mode="picker"
          projectId={projectId}
          onClose={() => setShowMediaLibrary(false)}
          onAdded={() => { onUpdate(); setShowMediaLibrary(false) }}
        />
      )}
    </div>
  )
}
