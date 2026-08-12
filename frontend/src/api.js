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

/**
 * Añade la sesión a TODA petición a /api, venga del envoltorio api() o de un
 * fetch suelto.
 *
 * Hay más de treinta llamadas con fetch crudo repartidas por los componentes.
 * Convertirlas una a una era invitar a que se escapara alguna y se rompiera una
 * pantalla en producción sin ruido, y no evitaba que la próxima que alguien
 * escriba vuelva a olvidarse. Interceptando en un solo punto no hay call site
 * que se pueda saltar la cabecera.
 *
 * Sólo toca rutas /api del propio origen: una petición a Pexels o a Cloudinary
 * no debe llevar nunca la sesión de LayerCut.
 */
function instalarInterceptor() {
  if (typeof window === "undefined" || window.__layercutFetch) return
  const original = window.fetch
  window.__layercutFetch = original

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || ""
    const esApiPropia = url.startsWith("/api") ||
      (url.startsWith(window.location.origin + "/api"))

    if (!esApiPropia) return original(input, init)

    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined))
    // Sin pisar una cabecera puesta a mano: api() ya la añade y no hay motivo
    // para que gane el interceptor.
    if (!headers.has("Authorization")) {
      const token = getToken()
      if (token) headers.set("Authorization", `Bearer ${token}`)
    }

    const res = await original(input, { ...init, headers })
    if (res.status === 401) {
      clearToken()
      window.dispatchEvent(new Event("auth:logout"))
    }
    return res
  }
}

instalarInterceptor()

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
  // El 401 lo maneja el interceptor, que cubre también los fetch sueltos.
  return fetch(path, opts)
}

export async function apiJson(path, options = {}) {
  const res = await api(path, options)
  return res.json()
}
