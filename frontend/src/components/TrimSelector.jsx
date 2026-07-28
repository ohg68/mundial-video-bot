import { useState, useRef, useEffect } from "react"

function formatTime(s) {
  if (!isFinite(s)) return "0:00"
  const m = Math.floor(s / 60)
  const sec = (s % 60).toFixed(1)
  return `${m}:${sec.padStart(4, "0")}`
}

export default function TrimSelector({ asset, mode, onClose, onTrimmed, onAddToProject }) {
  const duration = asset.duration || 0
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(duration)
  const [previewing, setPreviewing] = useState(false)
  const [trimming, setTrimming] = useState(false)
  const [trimError, setTrimError] = useState(null)
  const [adding, setAdding] = useState(false)
  const videoRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const clampStart = (v) => Math.min(Math.max(0, v), end - 0.1)
  const clampEnd = (v) => Math.max(Math.min(duration, v), start + 0.1)

  const seekTo = (t) => {
    if (videoRef.current) videoRef.current.currentTime = t
  }

  const handlePreview = () => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = start
    v.play().catch(() => {})
    setPreviewing(true)
  }

  const handleTimeUpdate = () => {
    const v = videoRef.current
    if (previewing && v && v.currentTime >= end) {
      v.pause()
      setPreviewing(false)
    }
  }

  const handleConfirmTrim = async () => {
    setTrimming(true)
    setTrimError(null)
    try {
      const res = await fetch(`/api/library/${asset.id}/trim`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end }),
      })
      const child = await res.json()
      if (!res.ok) {
        setTrimError(child.detail || "No se pudo recortar")
        setTrimming(false)
        return
      }
      pollRef.current = setInterval(async () => {
        const r = await fetch(`/api/library/${child.id}`)
        const data = await r.json()
        if (data.status === "ready") {
          clearInterval(pollRef.current)
          setTrimming(false)
          onTrimmed(data)
        } else if (data.status === "error") {
          clearInterval(pollRef.current)
          setTrimming(false)
          setTrimError(data.error || "Error al recortar")
        }
      }, 1500)
    } catch {
      setTrimError("Error de conexión")
      setTrimming(false)
    }
  }

  const selectionDuration = Math.max(0, end - start)

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-2xl w-full max-w-[520px] border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="m-0 text-sm font-medium">Seleccionar fragmento</h3>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <video
          ref={videoRef}
          src={`/api/library/${asset.id}/video`}
          controls
          onTimeUpdate={handleTimeUpdate}
          className="w-full max-h-[280px] rounded-lg bg-black mb-3"
        />

        {/* IN/OUT sliders */}
        <div className="relative h-8 mb-2">
          <input
            type="range" min={0} max={duration} step={0.1} value={start}
            onChange={e => setStart(clampStart(Number(e.target.value)))}
            className="dual-range absolute w-full accent-[#0C447C]"
          />
          <input
            type="range" min={0} max={duration} step={0.1} value={end}
            onChange={e => setEnd(clampEnd(Number(e.target.value)))}
            className="dual-range absolute w-full accent-amber-500"
          />
        </div>

        <div className="flex justify-between text-xs text-gray-500 mb-3">
          <span>IN: <button onClick={() => seekTo(start)} className="bg-transparent border-none cursor-pointer text-[#0C447C] font-medium p-0">{formatTime(start)}</button></span>
          <span>Duración: {formatTime(selectionDuration)}</span>
          <span>OUT: <button onClick={() => seekTo(end)} className="bg-transparent border-none cursor-pointer text-amber-600 font-medium p-0">{formatTime(end)}</button></span>
        </div>

        <p className="text-[11px] text-gray-400 mb-3">
          El recorte puede variar ±1-2s por limitaciones de codificación (cae en el fotograma clave más cercano).
        </p>

        <div className="flex gap-2 mb-3">
          <button onClick={handlePreview} className="btn-outline flex-1">▶ Previsualizar tramo</button>
        </div>

        {trimError && <div className="text-xs text-red-600 mb-3">{trimError}</div>}

        <div className="flex gap-2">
          {mode === "picker" && onAddToProject && (
            <button onClick={async () => { setAdding(true); await onAddToProject(); setAdding(false) }}
              disabled={adding} className="btn-outline flex-1">
              {adding ? "⏳" : "＋ Usar completo"}
            </button>
          )}
          <button
            onClick={handleConfirmTrim}
            disabled={trimming || selectionDuration < 0.2}
            className="btn-primary flex-1"
          >
            {trimming ? "⏳ Recortando..." : "✂ Confirmar recorte"}
          </button>
        </div>
      </div>
    </div>
  )
}
