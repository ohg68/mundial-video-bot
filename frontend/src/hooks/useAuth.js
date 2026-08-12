import { useState, useEffect, useCallback } from "react"
import { getToken, clearToken, api } from "../api"

/**
 * Estado de sesión. No hay login con usuario/contraseña: la sesión se obtiene
 * canjeando el código que emite el bot (ver LoginForm), así que acá sólo queda
 * comprobar si el token guardado sigue valiendo y poder cerrarla.
 */
export default function useAuth() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const checkAuth = useCallback(async () => {
    if (!getToken()) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const res = await api("/api/auth/me")
      if (res.ok) {
        setUser(await res.json())
      } else {
        // 401 ya limpia el token dentro de api(); acá sólo reflejamos el estado.
        clearToken()
        setUser(null)
      }
    } catch {
      // Sin red no se puede afirmar que la sesión sea inválida: no se borra el
      // token, sólo se queda sin usuario hasta el próximo intento.
      setUser(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    checkAuth()
    const onLogout = () => { setUser(null); setLoading(false) }
    window.addEventListener("auth:logout", onLogout)
    return () => window.removeEventListener("auth:logout", onLogout)
  }, [checkAuth])

  const logout = () => {
    clearToken()
    setUser(null)
  }

  return { user, loading, setUser, logout }
}
