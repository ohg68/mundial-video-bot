import { useState, useEffect, useRef } from "react"
import { setToken } from "../api"

/**
 * Entrada sin contraseña: el código lo emite el bot de Telegram con /entrar.
 *
 * No hay campo de contraseña porque no hay contraseña — nada que recordar ni que
 * recuperar. La llave de emergencia de Railway se pega en el mismo campo: el
 * backend la reconoce como sesión válida, así que un bot caído no deja a nadie
 * fuera.
 */
export default function LoginForm({ onLogin }) {
  const [code, setCode] = useState("")
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const [vias, setVias] = useState(null)
  const inputRef = useRef()

  useEffect(() => {
    fetch("/api/auth/status")
      .then(r => r.ok ? r.json() : null)
      .then(setVias)
      .catch(() => {})
    inputRef.current?.focus()
  }, [])

  const entrar = async (e) => {
    e?.preventDefault()
    const valor = code.trim()
    if (!valor) return
    setEnviando(true)
    setError(null)
    try {
      const res = await fetch("/api/auth/telegram/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: valor }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.detail || `Error ${res.status}`)
        setCode("")
        inputRef.current?.focus()
      } else {
        setToken(data.token)
        onLogin(data)
      }
    } catch {
      setError("Sin conexión con el servidor")
    }
    setEnviando(false)
  }

  return (
    <div className="min-h-dvh flex items-center justify-center bg-gray-50 px-5">
      <div className="w-full max-w-sm">
        <div className="text-center mb-7">
          <div className="text-4xl mb-3">🎬</div>
          <h1 className="text-xl font-medium text-gray-900 m-0">LayerCut</h1>
        </div>

        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <ol className="list-none p-0 m-0 mb-5 space-y-3 text-sm text-gray-600">
            <li className="flex gap-3">
              <span className="flex-none w-5 h-5 rounded-full bg-[#0C447C] text-white text-[11px]
                               flex items-center justify-center font-medium">1</span>
              <span>Escribile <code className="bg-gray-100 px-1.5 py-0.5 rounded text-[13px]
                                               text-gray-900">/entrar</code> al bot de Telegram</span>
            </li>
            <li className="flex gap-3">
              <span className="flex-none w-5 h-5 rounded-full bg-[#0C447C] text-white text-[11px]
                               flex items-center justify-center font-medium">2</span>
              <span>Pegá acá el código que te manda</span>
            </li>
          </ol>

          <form onSubmit={entrar}>
            <input
              ref={inputRef}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="000000"
              value={code}
              onChange={e => setCode(e.target.value)}
              className="w-full px-4 py-3 text-center text-2xl tracking-[0.3em] font-mono
                         border border-gray-300 rounded-xl focus:outline-none
                         focus:border-[#0C447C] focus:ring-2 focus:ring-blue-100"
            />
            {error && (
              <p className="text-[13px] text-red-600 mt-2.5 mb-0 text-center">{error}</p>
            )}
            <button
              type="submit"
              disabled={enviando || !code.trim()}
              className="w-full mt-4 py-3 rounded-xl bg-[#0C447C] text-white text-sm font-medium
                         border-none cursor-pointer disabled:bg-gray-300 disabled:cursor-default"
            >
              {enviando ? "Comprobando..." : "Entrar"}
            </button>
          </form>

          <p className="text-[11px] text-gray-400 mt-4 mb-0 text-center leading-relaxed">
            El código vence en 5 minutos y sirve una vez.<br />
            La sesión dura 30 días.
          </p>
        </div>

        {vias && !vias.telegram && (
          <p className="text-[12px] text-amber-700 bg-amber-50 border border-amber-200
                        rounded-lg p-3 mt-4 mb-0 leading-relaxed">
            La entrada por Telegram está sin configurar (falta
            <code className="mx-1">TELEGRAM_ALLOWED_CHATS</code> en Railway).
            {vias.recovery
              ? " Podés entrar con el token de emergencia."
              : " Tampoco hay token de emergencia definido."}
          </p>
        )}
      </div>
    </div>
  )
}
