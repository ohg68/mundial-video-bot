import { useState, useEffect } from "react"
import { apiJson } from "../api"

const PLATFORMS = [
  { key: "youtube",   label: "YouTube",   icon: "▶",  color: "text-red-600" },
  { key: "tiktok",    label: "TikTok",    icon: "♪",  color: "text-gray-800" },
  { key: "instagram", label: "Instagram", icon: "📷", color: "text-pink-600" },
]

export default function PublishPanel({ projectId, title, onClose }) {
  const [selected, setSelected] = useState([])
  const [publishing, setPublishing] = useState(false)
  const [results, setResults] = useState(null)
  const [shareUrl, setShareUrl] = useState(null)
  const [shareLinks, setShareLinks] = useState([])
  const [creatingShare, setCreatingShare] = useState(false)
  const [thumbGen, setThumbGen] = useState(false)
  const [scheduleMode, setScheduleMode] = useState(false)
  const [scheduleDate, setScheduleDate] = useState("")
  const [schedulePlatform, setSchedulePlatform] = useState("youtube")
  const [scheduled, setScheduled] = useState([])

  useEffect(() => {
    apiJson(`/api/share/${projectId}/links`).then(d => {
      if (d.links) setShareLinks(d.links)
    }).catch(() => {})
    apiJson(`/api/publish/${projectId}/schedule`).then(d => {
      if (d.posts) setScheduled(d.posts)
    }).catch(() => {})
  }, [projectId])

  const toggle = (key) => {
    setSelected(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }

  const handlePublish = async () => {
    if (selected.length === 0) return
    setPublishing(true)
    setResults(null)
    const data = await apiJson(`/api/publish/${projectId}/multi`, {
      method: "POST",
      body: { platforms: selected, title },
    })
    setResults(data.results || [])
    setPublishing(false)
  }

  const handleShare = async () => {
    setCreatingShare(true)
    const data = await apiJson(`/api/share/${projectId}/create`, {
      method: "POST",
      body: { expires_hours: 72 },
    })
    if (data.share_url) {
      setShareUrl(window.location.origin + data.share_url)
      setShareLinks(prev => [{ ...data, views: 0 }, ...prev])
    }
    setCreatingShare(false)
  }

  const handleDeleteLink = async (linkId) => {
    await apiJson(`/api/share/${projectId}/links/${linkId}`, { method: "DELETE" })
    setShareLinks(prev => prev.filter(l => l.id !== linkId))
  }

  const handleThumbnail = async () => {
    setThumbGen(true)
    await apiJson(`/api/publish/${projectId}/thumbnail`, { method: "POST", body: {} })
    setThumbGen(false)
  }

  const handleSchedule = async () => {
    if (!scheduleDate) return
    const data = await apiJson(`/api/publish/${projectId}/schedule`, {
      method: "POST",
      body: { platform: schedulePlatform, scheduled_at: scheduleDate, title },
    })
    if (data.id) setScheduled(prev => [...prev, data])
    setScheduleMode(false)
    setScheduleDate("")
  }

  const handleCancelScheduled = async (postId) => {
    await apiJson(`/api/publish/${projectId}/schedule/${postId}`, { method: "DELETE" })
    setScheduled(prev => prev.filter(p => p.id !== postId))
  }

  const copyUrl = () => {
    if (shareUrl) navigator.clipboard.writeText(shareUrl)
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50">
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full sm:w-[520px] sm:max-w-[90vw] max-h-[85dvh] flex flex-col border border-gray-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="m-0 text-base font-medium">Publicar y compartir</h2>
          <button onClick={onClose} className="bg-transparent border-none cursor-pointer text-lg text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Platform selection */}
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Plataformas</div>
            <div className="flex gap-2">
              {PLATFORMS.map(p => (
                <button
                  key={p.key}
                  onClick={() => toggle(p.key)}
                  className={`flex-1 py-2.5 rounded-lg border text-sm cursor-pointer transition-colors
                    ${selected.includes(p.key)
                      ? "bg-blue-50 border-blue-300 text-blue-800 font-medium"
                      : "bg-white border-gray-200 text-gray-600 hover:bg-gray-50"}`}
                >
                  <span className={p.color}>{p.icon}</span> {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Publish button */}
          <button
            onClick={handlePublish}
            disabled={publishing || selected.length === 0}
            className={`w-full py-2.5 rounded-lg border text-sm cursor-pointer transition-colors font-medium
              ${selected.length > 0
                ? "bg-[#185FA5] text-white border-[#185FA5] hover:bg-[#0C447C]"
                : "bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed"}`}
          >
            {publishing ? "Publicando..." : `Publicar en ${selected.length} plataforma${selected.length !== 1 ? "s" : ""}`}
          </button>

          {/* Results */}
          {results && (
            <div className="space-y-1.5">
              {results.map((r, i) => (
                <div key={i} className={`text-xs px-3 py-2 rounded-lg
                  ${r.status === "published" ? "bg-green-50 text-green-800" :
                    r.status === "mock" ? "bg-amber-50 text-amber-800" :
                    "bg-red-50 text-red-800"}`}>
                  <span className="font-medium">{r.platform}</span>: {r.status === "published" ? "Publicado" :
                    r.status === "mock" ? r.message : r.message || "Error"}
                  {r.url && <a href={r.url} target="_blank" rel="noopener" className="ml-2 underline">Ver</a>}
                </div>
              ))}
            </div>
          )}

          {/* Thumbnail */}
          <div className="flex items-center gap-2">
            <button onClick={handleThumbnail} disabled={thumbGen}
              className="btn-outline text-[13px]">
              {thumbGen ? "Generando..." : "🖼 Generar thumbnail"}
            </button>
          </div>

          {/* Schedule */}
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Programar publicación</div>
            {!scheduleMode ? (
              <button onClick={() => setScheduleMode(true)} className="btn-outline text-[13px]">
                🕐 Programar
              </button>
            ) : (
              <div className="flex gap-2 items-end flex-wrap">
                <select value={schedulePlatform} onChange={e => setSchedulePlatform(e.target.value)}
                  className="input-field text-[13px] py-1.5">
                  {PLATFORMS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select>
                <input type="datetime-local" value={scheduleDate}
                  onChange={e => setScheduleDate(e.target.value)}
                  className="input-field text-[13px] py-1.5" />
                <button onClick={handleSchedule} className="btn-outline text-[13px] bg-blue-50 text-blue-800">
                  Confirmar
                </button>
                <button onClick={() => setScheduleMode(false)} className="btn-outline text-[13px]">
                  Cancelar
                </button>
              </div>
            )}
            {scheduled.length > 0 && (
              <div className="mt-2 space-y-1">
                {scheduled.map(s => (
                  <div key={s.id} className="flex items-center justify-between text-xs bg-blue-50 rounded-lg px-3 py-2">
                    <span>
                      <span className="font-medium">{s.platform}</span> — {new Date(s.scheduled_at).toLocaleString("es")}
                      <span className={`ml-2 ${s.status === "pending" ? "text-blue-600" : "text-green-600"}`}>
                        ({s.status})
                      </span>
                    </span>
                    {s.status === "pending" && (
                      <button onClick={() => handleCancelScheduled(s.id)}
                        className="text-red-400 hover:text-red-600 bg-transparent border-none cursor-pointer text-xs">
                        ✕
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Share link */}
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Enlace para compartir</div>
            <button onClick={handleShare} disabled={creatingShare}
              className="btn-outline text-[13px]">
              {creatingShare ? "Creando..." : "🔗 Crear enlace (72h)"}
            </button>

            {shareUrl && (
              <div className="mt-2 flex items-center gap-2 bg-green-50 rounded-lg px-3 py-2">
                <input type="text" readOnly value={shareUrl}
                  className="flex-1 bg-transparent border-none text-xs text-green-800 outline-none" />
                <button onClick={copyUrl} className="text-xs text-green-700 bg-transparent border-none cursor-pointer font-medium">
                  Copiar
                </button>
              </div>
            )}

            {shareLinks.length > 0 && (
              <div className="mt-2 space-y-1">
                {shareLinks.map(l => (
                  <div key={l.id || l.token} className="flex items-center justify-between text-[11px] text-gray-500 px-1">
                    <span>{l.share_url} · {l.views || 0} vistas</span>
                    {l.id && (
                      <button onClick={() => handleDeleteLink(l.id)}
                        className="text-red-400 hover:text-red-600 bg-transparent border-none cursor-pointer text-[11px]">
                        ✕
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
