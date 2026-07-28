import { useState, useEffect } from "react"
import { apiJson } from "../api"

const STATUS_COLOR = {
  ready: "bg-emerald-500", pending: "bg-amber-400", empty: "bg-gray-200", error: "bg-red-500"
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
  projects, selected, onSelect, onNew, onOpenLibrary, onDeleted, onRefresh,
  categoryFilter, onCategoryFilter,
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
    <div className="flex flex-col h-full bg-white border-r border-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-4">
        <div className="flex items-center gap-2">
          <span className="text-lg leading-none">🎬</span>
          <span className="font-semibold text-[15px] tracking-tight">LayerCut</span>
        </div>
        <button
          onClick={() => { setBulkMode(!bulkMode); setChecked(new Set()) }}
          className={`px-2 py-1 rounded-md text-[11px] cursor-pointer border transition-colors
            ${bulkMode ? "bg-red-50 text-red-600 border-red-200" : "bg-transparent text-gray-400 border-gray-200 hover:bg-gray-50 hover:text-gray-600"}`}
        >
          {bulkMode ? "Cancelar" : "Seleccionar"}
        </button>
      </div>

      <div className="px-4 pb-3 flex gap-1.5">
        <button onClick={onNew} className="btn-primary flex-1 py-2 text-[13px]">
          + Nuevo vídeo
        </button>
        <button onClick={onOpenLibrary} className="btn-outline py-2 px-2.5 text-[13px]" title="Biblioteca de vídeos">
          📚
        </button>
      </div>

      {/* Category filter */}
      <div className="px-4 pb-3 flex gap-1.5 flex-wrap">
        <button
          onClick={() => onCategoryFilter("")}
          className={`px-2.5 py-1 rounded-full text-[10px] font-medium border cursor-pointer transition-colors
            ${!categoryFilter ? "bg-[#0C447C] text-white border-[#0C447C]" : "bg-transparent text-gray-400 border-gray-200 hover:border-gray-300"}`}
        >
          Todos
        </button>
        {CATEGORIES.map(cat => (
          <button
            key={cat}
            onClick={() => onCategoryFilter(categoryFilter === cat ? "" : cat)}
            className={`px-2.5 py-1 rounded-full text-[10px] border cursor-pointer transition-colors truncate max-w-[100px]
              ${categoryFilter === cat ? "bg-[#0C447C] text-white border-[#0C447C]" : "bg-transparent text-gray-400 border-gray-200 hover:border-gray-300"}`}
            title={cat}
          >
            {cat.split(" / ")[0]}
          </button>
        ))}
      </div>

      <div className="border-t border-gray-100" />

      {/* Bulk actions */}
      {bulkMode && checked.size > 0 && (
        <div className="flex items-center justify-between px-4 py-2 bg-red-50 border-b border-red-100">
          <span className="text-xs text-red-700">{checked.size} seleccionado(s)</span>
          <button
            onClick={handleBulkDelete}
            className="px-3 py-1 rounded-md text-xs bg-red-600 text-white border-none cursor-pointer hover:bg-red-700"
          >
            Eliminar
          </button>
        </div>
      )}

      {/* Project list */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {projects.length === 0 && (
          <div className="text-center py-10 px-4">
            <p className="text-[13px] text-gray-400 m-0">Sin proyectos aún</p>
            <p className="text-[11px] text-gray-300 mt-1 m-0">Creá uno con el botón de arriba</p>
          </div>
        )}
        {projects.map(p => {
          const layerStatuses = Object.values(p.layers || {})
          const allReady = layerStatuses.filter(s => s === "ready").length
          const isSelected = selected?.id === p.id
          return (
            <div
              key={p.id}
              onClick={() => bulkMode ? toggleCheck(p.id) : onSelect(p)}
              className={`group px-3 py-2.5 mb-1 rounded-xl cursor-pointer flex gap-2.5 items-start transition-colors
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
                  <span className={`text-[13px] font-medium leading-snug truncate ${isSelected ? "text-[#0C447C]" : "text-gray-700"}`}>
                    {p.title}
                  </span>
                  {!bulkMode && (
                    <div className="flex gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={(e) => handleDuplicate(e, p.id)} className="bg-transparent border-none cursor-pointer text-xs text-gray-300 hover:text-gray-600 p-0.5" title="Duplicar">⧉</button>
                      <button onClick={(e) => handleDelete(e, p.id)} className="bg-transparent border-none cursor-pointer text-xs text-gray-300 hover:text-red-500 p-0.5" title="Eliminar">✕</button>
                    </div>
                  )}
                </div>
                <div className="flex gap-2 items-center mt-0.5">
                  {p.match_date && <span className="text-[10.5px] text-gray-400">{p.match_date}</span>}
                  {p.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{p.category.split(" / ")[0]}</span>
                  )}
                </div>
                <div className="flex gap-1 flex-wrap mt-1.5 items-center">
                  {Object.entries(p.layers || {}).map(([layer, status]) => (
                    <span
                      key={layer}
                      className={`w-1.5 h-1.5 rounded-full inline-block ${STATUS_COLOR[status] || "bg-gray-300"}`}
                      title={`${layer}: ${status}`}
                    />
                  ))}
                  <span className="text-[10.5px] text-gray-400 ml-1">{allReady}/5</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer: stats */}
      {stats && (
        <div className="px-4 py-2.5 border-t border-gray-100 text-[11px] text-gray-400">
          {stats.project_count} proyecto{stats.project_count === 1 ? "" : "s"} · {formatSize(stats.total_bytes)}
        </div>
      )}
    </div>
  )
}
