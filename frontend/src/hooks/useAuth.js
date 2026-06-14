import { useState, useEffect, useCallback } from "react"
import { getToken, setToken, clearToken, apiJson } from "../api"

export default function useAuth() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const checkAuth = useCallback(async () => {
    const token = getToken()
    if (!token) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const data = await apiJson("/api/auth/me")
      if (data.username) {
        setUser(data)
      } else {
        clearToken()
        setUser(null)
      }
    } catch {
      clearToken()
      setUser(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    checkAuth()
    const handleLogout = () => { setUser(null); setLoading(false) }
    window.addEventListener("auth:logout", handleLogout)
    return () => window.removeEventListener("auth:logout", handleLogout)
  }, [checkAuth])

  const login = async (username, password) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json()
    if (res.ok && data.token) {
      setToken(data.token)
      setUser({ username: data.username, user_id: data.user_id })
      return { ok: true }
    }
    return { ok: false, error: data.detail || "Error" }
  }

  const register = async (username, password) => {
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json()
    if (res.ok && data.token) {
      setToken(data.token)
      setUser({ username: data.username, user_id: data.user_id })
      return { ok: true }
    }
    return { ok: false, error: data.detail || "Error" }
  }

  const logout = () => {
    clearToken()
    setUser(null)
  }

  return { user, loading, login, register, logout }
}
