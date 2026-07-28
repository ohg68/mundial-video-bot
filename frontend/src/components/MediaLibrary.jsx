import { useState, useEffect, useRef } from "react"
import TrimSelector from "./TrimSelector"

const STATUS_LABEL = {
  pending: "En cola...",
  downloading: "Descargando...",
  trimming: "Recortando...",
  ready: "Listo",
  error: "Error",
}

function formatDuration(s) {
  if (!s) return "--:--"
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, "0")}`
}

export default function MediaLibrary({ onClose, mode = "global", projectId = null, onAdded = null }) {
  const [url, setUrl] = useState("")
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState(null)
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedAsset, setSelectedAsset] = useState(null)
  const [addingId, setAddingId] = useState(null)
  const pollRef = useRef(null)

  const fetchAssets = async () => {
    const res = await fetch("/api/library/")
    const data = await res.json()
    setAssets(data.assets || [])
    setLoading(false)
  }

  useEffect(() => { fetchAssets() }, [])

  useEffect(() => {
    const hasPending = assets.some(a => ["pending", "downloading", "trimming"].includes(a.status))
    if (hasPending && !pollRef.current) {
      pollRef.current = setInterval(fetchAssets, 2000)
    } else if (!hasPending && pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  }, [assets])

  const handleImport = async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    setImporting(true)
    setImportError(null)
    try {
      const res = await fetch("/api/library/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: trimmed }),
      })
      const data = await res.json()
      if (!res.ok) {
        setImportError(data.detail || "No se pudo importar")
      } else {
        setUrl("")
        setAssets(prev => [data, ...prev])
      }
    } catch {
      setImportError("Error de conexión")
    }
    setImporting(false)
  }

  const handleDelete = async (e, id) => {
    e.stopPropagation()
    if (!confirm("¿Borrar este vídeo de la biblioteca?")) return
    await fetch(`/api/library/${id}`, { method: "DELETE" })
    setAssets(prev => prev.filter(a => a.id !== id && a.parent_id !== id))
  }

  const handleAddToProject = async (e, assetId) => {
    e.stopPropagation()
    if (!projectId) return
    setAddingId(assetId)
    try {
      const res = await fetch(`/api/library/${assetId}/add-to-project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId }),
      })
      if (res.ok) {
        onAdded?.()
      }
    } finally {
      setAddingId(null)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full sm:w-[720px] sm:max-w-[95vw] max-h-[90dvh] flex flex-col border border-gray-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="m-0 text-base font-medium">📚 Biblioteca de vídeos</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600">✕</button>
        </div>

        {/* Import form */}
        <div className="px-5 py-3 border-b border-gray-100 space-y-1.5">
          <div className="flex gap-2">
            <input
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleImport()}
              placeholder="Pegá una URL de YouTube u otro sitio compatible..."
              className="input-field flex-1"
            />
            <button
              onClick={handleImport}
              disabled={importing || !url.trim()}
              className="btn-primary whitespace-nowrap"
            >
              {importing ? "⏳" : "⬇ Importar"}
            </button>
          </div>
          {importError && <div className="text-xs text-red-600">{importError}</div>}
          <p className="text-[11px] text-gray-400 m-0">
            Solo funciona con contenido públicamente accesible; el sistema no evade DRM ni restricciones de licencia.
          </p>
        </div>

        {/* Grid */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && <div className="text-center py-12 text-gray-400 text-sm">Cargando...</div>}
          {!loading && assets.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-gray-400 text-sm">
              <span className="text-3xl mb-2">📚</span>
              <p>Todavía no importaste ningún vídeo</p>
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            {assets.map(asset => {
              const inFlight = ["pending", "downloading", "trimming"].includes(asset.status)
              const isError = asset.status === "error"
              return (
                <div
                  key={asset.id}
                  onClick={() => asset.status === "ready" && setSelectedAsset(asset)}
                  className={`relative rounded-lg overflow-hidden border transition-all
                    ${asset.status === "ready" ? "cursor-pointer border-gray-200 hover:border-[#0C447C]" : "border-gray-100"}`}
                  title={isError ? asset.error : undefined}
                >
                  {asset.status === "ready" ? (
                    <img
                      src={`/api/library/${asset.id}/thumbnail`}
                      alt=""
                      className="w-full h-24 sm:h-28 object-cover bg-gray-100"
                      loading="lazy"
                    />
                  ) : (
                    <div className={`w-full h-24 sm:h-28 flex items-center justify-center text-2xl
                      ${isError ? "bg-red-50" : "bg-gray-100"}`}>
                      {isError ? "⚠️" : "⏳"}
                    </div>
                  )}
                  {asset.parent_id && (
                    <div className="absolute top-1.5 left-1.5 text-[10px] px-1.5 py-0.5 rounded bg-black/60 text-white">✂ recorte</div>
                  )}
                  <button
                    onClick={(e) => handleDelete(e, asset.id)}
                    className="absolute top-1.5 right-1.5 w-5 h-5 rounded-full bg-black/60 text-white border-none cursor-pointer text-[11px] flex items-center justify-center hover:bg-red-600"
                  >✕</button>
                  <div className="p-1.5">
                    <div className={`text-[11px] truncate ${isError ? "text-red-600" : "text-gray-600"}`}>
                      {isError ? "Error" : STATUS_LABEL[asset.status]}
                    </div>
                    <div className="flex justify-between text-[10px] text-gray-400">
                      <span>{asset.source_type}</span>
                      {asset.status === "ready" && <span>{formatDuration(asset.duration)}</span>}
                    </div>
                  </div>
                  {mode === "picker" && asset.status === "ready" && (
                    <button
                      onClick={(e) => handleAddToProject(e, asset.id)}
                      disabled={addingId === asset.id}
                      className="w-full py-1 text-[11px] border-t border-gray-100 bg-blue-50 text-[#0C447C] border-l-0 border-r-0 border-b-0 cursor-pointer hover:bg-blue-100"
                    >
                      {addingId === asset.id ? "⏳" : "＋ Añadir al proyecto"}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {selectedAsset && (
        <TrimSelector
          asset={selectedAsset}
          mode={mode}
          onClose={() => setSelectedAsset(null)}
          onTrimmed={(child) => {
            setAssets(prev => [child, ...prev])
            setSelectedAsset(null)
          }}
          onAddToProject={projectId ? async () => {
            setAddingId(selectedAsset.id)
            try {
              const res = await fetch(`/api/library/${selectedAsset.id}/add-to-project`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: projectId }),
              })
              if (res.ok) onAdded?.()
            } finally {
              setAddingId(null)
              setSelectedAsset(null)
            }
          } : null}
        />
      )}
    </div>
  )
}
