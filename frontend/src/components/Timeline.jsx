const LAYER_CONFIG = [
  { key: "video",     label: "Vídeo",     color: "#0C447C", bg: "#E6F1FB" },
  { key: "audio",     label: "Narración",  color: "#27500A", bg: "#EAF3DE" },
  { key: "music",     label: "Música",     color: "#633806", bg: "#FAEEDA" },
  { key: "subtitles", label: "Subtítulos", color: "#3C3489", bg: "#EEEDFE" },
  { key: "overlay",   label: "Overlay",    color: "#712B13", bg: "#FAECE7" },
]

export default function Timeline({ layers }) {
  if (!layers) return null

  const durations = LAYER_CONFIG.map(l => layers[l.key]?.duration || 0)
  const maxDuration = Math.max(...durations, 1)

  const formatTime = (s) => {
    if (!s) return "—"
    const m = Math.floor(s / 60)
    const sec = Math.round(s % 60)
    return m > 0 ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
      <div className="flex items-center justify-between mb-2.5">
        <span className="text-xs font-medium text-gray-600">Timeline</span>
        <span className="text-[11px] text-gray-400">{formatTime(maxDuration)} total</span>
      </div>
      <div className="space-y-1.5">
        {LAYER_CONFIG.map(layer => {
          const info = layers[layer.key]
          const duration = info?.duration || 0
          const width = maxDuration > 0 ? (duration / maxDuration) * 100 : 0
          const exists = info?.exists

          return (
            <div key={layer.key} className="flex items-center gap-2">
              <span className="text-[10px] w-[52px] shrink-0 text-right" style={{ color: layer.color }}>
                {layer.label}
              </span>
              <div className="flex-1 h-5 bg-gray-50 rounded-sm overflow-hidden relative">
                {exists && width > 0 && (
                  <div
                    className="h-full rounded-sm transition-all duration-500"
                    style={{ width: `${width}%`, backgroundColor: layer.bg, borderLeft: `3px solid ${layer.color}` }}
                  />
                )}
                {!exists && (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-[9px] text-gray-300">sin archivo</span>
                  </div>
                )}
              </div>
              <span className="text-[10px] text-gray-400 w-[36px] shrink-0">
                {exists ? formatTime(duration) : "—"}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
