import { useState } from "react"

const LAYER_LABELS = {
  video: "Vídeo",
  audio: "Narración",
  music: "Música",
  output: "Render final",
}

export default function VideoPreview({ projectId, layers }) {
  const [activeLayer, setActiveLayer] = useState("output")

  const available = []
  if (layers?.output?.exists) available.push("output")
  if (layers?.video?.exists) available.push("video")
  if (layers?.audio?.exists) available.push("audio")
  if (layers?.music?.exists) available.push("music")

  const current = available.includes(activeLayer) ? activeLayer : available[0]
  if (!current) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
        <div className="flex flex-col items-center justify-center py-8 text-gray-400">
          <span className="text-3xl mb-2">🎬</span>
          <p className="text-sm">Sin archivos para previsualizar</p>
        </div>
      </div>
    )
  }

  const isVideo = current === "video" || current === "output"
  const isAudio = current === "audio" || current === "music"

  const src = current === "output"
    ? `/api/render/${projectId}/download`
    : `/api/layers/${projectId}/download/${current}`

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden mb-3">
      {/* Layer selector tabs */}
      <div className="flex border-b border-gray-100">
        {available.map(key => (
          <button
            key={key}
            onClick={() => setActiveLayer(key)}
            className={`flex-1 py-2 text-xs cursor-pointer border-none transition-colors
              ${current === key
                ? "bg-blue-50 text-[#0C447C] font-medium border-b-2 border-[#0C447C]"
                : "bg-transparent text-gray-400 hover:bg-gray-50"}`}
          >
            {LAYER_LABELS[key] || key}
          </button>
        ))}
      </div>

      {/* Player */}
      <div className="p-3">
        {isVideo && (
          <video
            key={src}
            src={src}
            controls
            playsInline
            className="w-full rounded-lg bg-black max-h-[300px]"
          />
        )}
        {isAudio && (
          <div className="flex flex-col items-center gap-3 py-4">
            <span className="text-3xl">{current === "audio" ? "🎙" : "🎵"}</span>
            <audio key={src} src={src} controls className="w-full" />
          </div>
        )}
        {layers?.[current] && (
          <div className="mt-2 flex justify-between text-[11px] text-gray-400">
            <span>{layers[current].duration > 0 ? `${layers[current].duration}s` : ""}</span>
            <span>{layers[current].size_bytes > 0
              ? `${(layers[current].size_bytes / 1024 / 1024).toFixed(1)} MB` : ""}</span>
          </div>
        )}
      </div>
    </div>
  )
}
