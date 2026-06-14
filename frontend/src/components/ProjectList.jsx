import { useState, useEffect } from "react"
import { apiJson } from "../api"

const STATUS_COLOR = {
  ready: "bg-green-500", pending: "bg-amber-400", empty: "bg-gray-300", error: "bg-red-500"
}

const CATEGORIES = [
  "Marketing / Promoción",
  "Educación / Tutorial",
  "Noticias / Actualidad",
  "Entretenimiento",
  "Corporativo / Empresa",
  "Producto / Demo",
  "Redes sociales",
]

export default function ProjectList({
  projects, selected, onSelect, onNew, onDeleted, onRefresh,
  categoryFilter, onCategoryFilter, user, onLogout,
}) {
  const [bulkMode, setBulkMode] = useState(false)
  const [checked, setChecked] = useState(new Set())
  const [stats, setStats] = useState(null)

  useEffect(() => {
    apiJson("/api/projects/stats").then(setStats).catch(() => {})
  }, [projects.length])

  const toggleCheck = (id) => {
    setChecked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleBulkDelete = async () => {
    if (!checked.size || !confirm(`¿Eliminar ${checked.size} proyecto(s)?`)) return
    await apiJson("/api/projects/bulk-delete", {
      method: "POST",
      body: { project_ids: [...checked] },
    })
    checked.forEach(id => onDeleted(id))
    setChecked(new Set())
    setBulkMode(false)
    onRefresh()
  }

  const handleDuplicate = async (e, id) => {
    e.stopPropagation()
    await apiJson(`/api/projects/${id}/duplicate`, { method: "POST" })
    onRefresh()
  }

  const handleDelete = async (e, id) => {
    e.stopPropagation()
    if (!confirm("¿Eliminar este proyecto?")) return
    await apiJson(`/api/projects/${id}`, { method: "DELETE" })
    onDeleted(id)
  }

  const formatSize = (bytes) => {
    if (!bytes) return "0 B"
    const units = ["B", "KB", "MB", "GB"]
    const i = Math.floor(Math.log(bytes) / Math.log(1024))
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
  }

  return (
    <div className="flex flex-col h-full bg-white border-r border-gray-200">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <span className="font-medium text-[15px]">LayerCut</span>
        <div className="flex gap-2 items-center">
          <button
            onClick={() => { setBulkMode(!bulkMode); setChecked(new Set()) }}
            className={`px-2 py-1 rounded text-xs cursor-pointer border transition-colors
              ${bulkMode ? "bg-red-50 text-red-600 border-red-200" : "bg-transparent text-gray-500 border-gray-200 hover:bg-gray-50"}`}
          >
            {bulkMode ? "Cancelar" : "Seleccionar"}
          </button>
          <button
            onClick={onNew}
            className="px-2.5 py-1 rounded-md border border-gray-300 bg-transparent cursor-pointer text-[13px] hover:bg-gray-50 transition-colors"
          >
            + Nuevo
          </button>
        </div>
      </div>

      {/* Category filter */}
      <div className="px-3 py-2 border-b border-gray-100 flex gap-1 flex-wrap">
        <button
          onClick={() => onCategoryFilter("")}
          className={`px-2 py-0.5 rounded text-[10px] border cursor-pointer transition-colors
            ${!categoryFilter ? "bg-[#0C447C] text-white border-[#0C447C]" : "bg-transparent text-gray-400 border-gray-200"}`}
        >
          Todos
        </button>
        {CATEGORIES.map(cat => (
          <button
            key={cat}
            onClick={() => onCategoryFilter(categoryFilter === cat ? "" : cat)}
            className={`px-2 py-0.5 rounded text-[10px] border cursor-pointer transition-colors truncate max-w-[90px]
              ${categoryFilter === cat ? "bg-[#0C447C] text-white border-[#0C447C]" : "bg-transparent text-gray-400 border-gray-200"}`}
            title={cat}
          >
            {cat.split(" / ")[0]}
          </button>
        ))}
      </div>

      {/* Bulk actions */}
      {bulkMode && checked.size > 0 && (
        <div className="flex items-center justify-between px-4 py-2 bg-red-50 border-b border-red-100">
          <span className="text-xs text-red-700">{checked.size} seleccionado(s)</span>
          <button
            onClick={handleBulkDelete}
            className="px-3 py-1 rounded text-xs bg-red-600 text-white border-none cursor-pointer hover:bg-red-700"
          >
            Eliminar
          </button>
        </div>
      )}

      {/* Project list */}
      <div className="flex-1 overflow-y-auto">
        {projects.length === 0 && (
          <p className="p-4 text-[13px] text-gray-400">Sin proyectos aún</p>
        )}
        {projects.map(p => {
          const layerStatuses = Object.values(p.layers || {})
          const allReady = layerStatuses.filter(s => s === "ready").length
          const isSelected = selected?.id === p.id
          return (
            <div
              key={p.id}
              onClick={() => bulkMode ? toggleCheck(p.id) : onSelect(p)}
              className={`px-4 py-3 cursor-pointer border-b border-gray-100 flex gap-3 items-start transition-colors
                ${isSelected && !bulkMode ? "bg-blue-50" : "hover:bg-gray-50"}`}
            >
              {bulkMode && (
                <input
                  type="checkbox"
                  checked={checked.has(p.id)}
                  onChange={() => toggleCheck(p.id)}
                  className="mt-1 w-4 h-4 accent-[#0C447C]"
                />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex justify-between items-start gap-2">
                  <span className="text-[13px] font-medium leading-snug truncate">{p.title}</span>
                  {!bulkMode && (
                    <div className="flex gap-1 shrink-0">
                      <button onClick={(e) => handleDuplicate(e, p.id)} className="bg-transparent border-none cursor-pointer text-xs text-gray-300 hover:text-gray-600 p-0.5" title="Duplicar">⧉</button>
                      <button onClick={(e) => handleDelete(e, p.id)} className="bg-transparent border-none cursor-pointer text-xs text-gray-300 hover:text-red-500 p-0.5" title="Eliminar">✕</button>
                    </div>
                  )}
                </div>
                <div className="flex gap-2 items-center mt-0.5">
                  {p.match_date && <span className="text-[11px] text-gray-400">{p.match_date}</span>}
                  {p.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{p.category.split(" / ")[0]}</span>
                  )}
                </div>
                <div className="flex gap-1 flex-wrap mt-1 items-center">
                  {Object.entries(p.layers || {}).map(([layer, status]) => (
                    <span
                      key={layer}
                      className={`w-2 h-2 rounded-full inline-block ${STATUS_COLOR[status] || "bg-gray-300"}`}
                      title={`${layer}: ${status}`}
                    />
                  ))}
                  <span className="text-[11px] text-gray-400 ml-1">{allReady}/5 capas</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer: stats + user */}
      <div className="px-4 py-2.5 border-t border-gray-200 bg-gray-50 flex justify-between items-center">
        <div className="text-[11px] text-gray-500">
          {stats ? `${stats.project_count} proy · ${formatSize(stats.total_bytes)}` : ""}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400">{user?.username}</span>
          <button
            onClick={onLogout}
            className="text-[10px] text-red-400 bg-transparent border-none cursor-pointer hover:text-red-600"
          >
            Salir
          </button>
        </div>
      </div>
    </div>
  )
}
