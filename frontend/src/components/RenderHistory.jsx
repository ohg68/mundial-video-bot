import { useState, useEffect } from "react"
import { apiJson } from "../api"

export default function RenderHistory({ projectId, onClose }) {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchHistory = async () => {
    setLoading(true)
    const data = await apiJson(`/api/render/${projectId}/history`)
    setHistory(data.history || [])
    setLoading(false)
  }

  useEffect(() => { fetchHistory() }, [projectId])

  const handleDelete = async (filename) => {
    if (!confirm("¿Eliminar este render?")) return
    await apiJson(`/api/render/${projectId}/history/${filename}`, { method: "DELETE" })
    setHistory(prev => prev.filter(h => h.filename !== filename))
  }

  const formatDate = (iso) => {
    if (!iso) return ""
    const d = new Date(iso)
    return d.toLocaleDateString("es", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
  }

  const totalSize = history.reduce((s, h) => s + (h.size_bytes || 0), 0)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full sm:w-[480px] sm:max-w-[90vw] max-h-[80dvh] flex flex-col border border-gray-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="m-0 text-base font-medium">Historial de renders</h2>
            <span className="text-[11px] text-gray-400">
              {history.length} versiones · {(totalSize / 1024 / 1024).toFixed(1)} MB
            </span>
          </div>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600">✕</button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="p-6 text-center text-gray-400 text-sm">Cargando...</div>
          )}
          {!loading && history.length === 0 && (
            <div className="p-6 text-center text-gray-400 text-sm">
              <span className="text-2xl block mb-2">📂</span>
              Sin renders anteriores
            </div>
          )}
          {history.map((h, i) => (
            <div key={h.filename} className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 hover:bg-gray-50">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium
                    ${h.quality === "quick" ? "bg-amber-50 text-amber-700" : "bg-green-50 text-green-700"}`}>
                    {h.quality === "quick" ? "540p" : "Full"}
                  </span>
                  <span className="text-[11px] text-gray-600">{formatDate(h.created_at)}</span>
                  {i === 0 && <span className="text-[10px] text-blue-500 font-medium">Último</span>}
                </div>
                <span className="text-[11px] text-gray-400">{h.size_mb} MB</span>
              </div>
              <div className="flex gap-1.5 shrink-0">
                <a
                  href={`/api/render/${projectId}/history/${h.filename}`}
                  download
                  className="btn-action no-underline"
                >
                  ⬇
                </a>
                <button
                  onClick={() => handleDelete(h.filename)}
                  className="btn-action text-red-400 hover:text-red-600 hover:bg-red-50"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
