import { useState } from "react"

export default function LoginForm({ onAuth }) {
  const [mode, setMode] = useState("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError("")
    setLoading(true)
    const result = await onAuth(mode, username, password)
    setLoading(false)
    if (!result.ok) setError(result.error)
  }

  return (
    <div className="flex items-center justify-center h-[100dvh] bg-gray-50">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl border border-gray-200 p-8 w-[360px] max-w-[90vw]">
        <div className="text-center mb-6">
          <div className="text-3xl mb-2">🎬</div>
          <h1 className="text-xl font-semibold text-gray-900">LayerCut</h1>
          <p className="text-sm text-gray-400 mt-1">
            {mode === "login" ? "Inicia sesión" : "Crea tu cuenta"}
          </p>
        </div>

        {error && (
          <div className="mb-4 p-2.5 rounded-lg bg-red-50 text-red-600 text-xs text-center">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="Usuario"
            className="input-field"
            required
            autoFocus
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Contraseña"
            className="input-field"
            required
            minLength={4}
          />
          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full py-2.5 rounded-lg bg-[#0C447C] text-white text-sm font-medium border-none cursor-pointer hover:bg-[#185FA5] disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "⏳" : mode === "login" ? "Entrar" : "Crear cuenta"}
          </button>
        </div>

        <div className="text-center mt-4">
          <button
            type="button"
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError("") }}
            className="text-xs text-[#0C447C] bg-transparent border-none cursor-pointer hover:underline"
          >
            {mode === "login" ? "¿No tienes cuenta? Regístrate" : "¿Ya tienes cuenta? Inicia sesión"}
          </button>
        </div>
      </form>
    </div>
  )
}
