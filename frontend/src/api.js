const TOKEN_KEY = "layercut_token"

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function isLoggedIn() {
  return !!getToken()
}

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function api(path, options = {}) {
  const { body, method = "GET", headers = {}, ...rest } = options
  const opts = {
    method,
    headers: {
      ...authHeaders(),
      ...headers,
    },
    ...rest,
  }
  if (body && !(body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json"
    opts.body = JSON.stringify(body)
  } else if (body) {
    opts.body = body
  }
  const res = await fetch(path, opts)
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event("auth:logout"))
  }
  return res
}

export async function apiJson(path, options = {}) {
  const res = await api(path, options)
  return res.json()
}
