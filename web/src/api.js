// Обёртка над fetch: токен, ошибки с detail от FastAPI, выход при 401.

export function getAuth() {
  const raw = localStorage.getItem('auth')
  return raw ? JSON.parse(raw) : null
}

export function setAuth(auth) {
  if (auth) localStorage.setItem('auth', JSON.stringify(auth))
  else localStorage.removeItem('auth')
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail)
    this.status = status
  }
}

export async function api(path, { method = 'GET', body, params } = {}) {
  const url = new URL(path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    }
  }
  const headers = { 'Content-Type': 'application/json' }
  const auth = getAuth()
  if (auth) headers['Authorization'] = `Bearer ${auth.token}`

  const resp = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (resp.status === 401 && auth) {
    setAuth(null)
    window.location.href = '/login'
    throw new ApiError(401, 'Сессия истекла')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const data = await resp.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* тело не JSON */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.status === 204 ? null : resp.json()
}

// Печатные формы требуют Authorization — открываем через blob.
export async function openReport(path, params, filename) {
  const url = new URL(path, window.location.origin)
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v)
  const auth = getAuth()
  const resp = await fetch(url, { headers: { Authorization: `Bearer ${auth.token}` } })
  if (!resp.ok) throw new ApiError(resp.status, 'Не удалось сформировать отчёт')
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  if (filename) {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    a.click()
  } else {
    window.open(objectUrl, '_blank')
  }
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}
