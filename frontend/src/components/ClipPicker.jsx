import { useState } from "react"

const SOURCES = [
  { key: "pexels", label: "Pexels" },
  { key: "pixabay", label: "Pixabay" },
  { key: "coverr", label: "Coverr" },
  { key: "youtube", label: "YouTube CC" },
]

export default function ClipPicker({ projectId, onClose, onClipsSelected }) {
  const [query, setQuery] = useState("")
  const [activeSources, setActiveSources] = useState(["pexels", "pixabay"])
  const [clips, setClips] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [searching, setSearching] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const toggleSource = (key) => {
    setActiveSources(prev =>
      prev.includes(key) ? prev.filter(s => s !== key) : [...prev, key]
    )
  }

  const handleSearch = async () => {
    if (!query.trim() || activeSources.length === 0) return
    setSearching(true)
    setClips([])
    setSelected(new Set())
    const res = await fetch(
      `/api/sources/clips/search?q=${encodeURIComponent(query)}&sources=${activeSources.join(",")}&count=12`
    )
    const data = await res.json()
    setClips(data.clips || [])
    setSearching(false)
  }

  const toggleSelect = (id) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleDownload = async () => {
    setDownloading(true)
    const selectedClips = clips.filter(c => selected.has(c.id))
    const results = []
    for (const clip of selectedClips) {
      const res = await fetch("/api/sources/clips/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clip, project_id: projectId }),
      })
      if (res.ok) {
        const data = await res.json()
        results.push(data)
      }
    }
    setDownloading(false)
    onClipsSelected(results)
    onClose()
  }

  const formatDuration = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full sm:w-[680px] sm:max-w-[95vw] max-h-[90dvh] flex flex-col border border-gray-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="m-0 text-base font-medium">Buscar clips de vídeo</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600">✕</button>
        </div>

        {/* Search bar + source toggles */}
        <div className="px-5 py-3 space-y-2.5 border-b border-gray-100">
          <div className="flex gap-2">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSearch()}
              placeholder="Buscar clips: futbol, gol, estadio..."
              className="input-field flex-1"
            />
            <button
              onClick={handleSearch}
              disabled={searching || !query.trim()}
              className="px-4 py-1.5 rounded-lg bg-[#0C447C] text-white text-sm border-none cursor-pointer hover:bg-[#185FA5] disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              {searching ? "⏳" : "🔍"}
            </button>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {SOURCES.map(s => (
              <button
                key={s.key}
                onClick={() => toggleSource(s.key)}
                className={`px-2.5 py-1 rounded-md text-xs border cursor-pointer transition-colors
                  ${activeSources.includes(s.key)
                    ? "bg-blue-50 border-blue-300 text-[#0C447C]"
                    : "bg-transparent border-gray-200 text-gray-400"}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Clips grid */}
        <div className="flex-1 overflow-y-auto p-4">
          {clips.length === 0 && !searching && (
            <div className="flex flex-col items-center justify-center py-12 text-gray-400 text-sm">
              <span className="text-3xl mb-2">🎬</span>
              <p>Busca clips de vídeo en múltiples fuentes</p>
            </div>
          )}
          {searching && (
            <div className="flex items-center justify-center py-12 text-gray-400">
              Buscando clips...
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            {clips.map(clip => (
              <div
                key={clip.id}
                onClick={() => toggleSelect(clip.id)}
                className={`relative rounded-lg overflow-hidden cursor-pointer border-2 transition-all
                  ${selected.has(clip.id) ? "border-[#0C447C] ring-2 ring-blue-200" : "border-transparent hover:border-gray-300"}`}
              >
                {clip.thumbnail ? (
                  <img
                    src={clip.thumbnail}
                    alt={clip.title}
                    className="w-full h-24 sm:h-28 object-cover bg-gray-100"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-full h-24 sm:h-28 bg-gray-200 flex items-center justify-center text-2xl">🎬</div>
                )}
                {selected.has(clip.id) && (
                  <div className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-[#0C447C] text-white flex items-center justify-center text-xs font-bold">✓</div>
                )}
                <div className="p-1.5">
                  <div className="text-[11px] text-gray-600 truncate">{clip.title || clip.source}</div>
                  <div className="flex justify-between text-[10px] text-gray-400">
                    <span>{clip.source}</span>
                    {clip.duration > 0 && <span>{formatDuration(clip.duration)}</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        {selected.size > 0 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 bg-gray-50">
            <span className="text-sm text-gray-600">{selected.size} clip(s) seleccionado(s)</span>
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="px-4 py-2 rounded-lg bg-[#0C447C] text-white text-sm border-none cursor-pointer hover:bg-[#185FA5] disabled:bg-gray-300 transition-colors"
            >
              {downloading ? "⏳ Descargando..." : "⬇ Descargar seleccionados"}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
