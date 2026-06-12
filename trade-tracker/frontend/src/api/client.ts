// Local dev (Vite proxy) and Docker (nginx) both forward /api -> backend with the
// prefix stripped. In production (Vercel), set VITE_API_BASE_URL to the Railway
// API URL (e.g. https://dekalb-trade-tracker-api.up.railway.app, no trailing slash) -
// the browser then calls the API directly and CORS (FRONTEND_URL on the backend)
// allows it.
const BASE = import.meta.env.VITE_API_BASE_URL || '/api'
const TOKEN_KEY = 'dekalb_id_token'
const USER_KEY = 'dekalb_user'

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function handle401() {
  localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY)
  window.location.href = '/login'
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (res.status === 401) { handle401(); throw new Error('Unauthenticated') }
  if (!res.ok) { const t = await res.text().catch(() => res.statusText); throw new Error(`${res.status}: ${t}`) }
  return res.json()
}

export async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) })
  if (res.status === 401) { handle401(); throw new Error('Unauthenticated') }
  if (!res.ok) { const t = await res.text().catch(() => res.statusText); throw new Error(`${res.status}: ${t}`) }
  return res.json()
}

export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: authHeaders(), body: form })
  if (res.status === 401) { handle401(); throw new Error('Unauthenticated') }
  if (!res.ok) { const t = await res.text().catch(() => res.statusText); throw new Error(`${res.status}: ${t}`) }
  return res.json()
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: body !== undefined ? JSON.stringify(body) : undefined })
  if (res.status === 401) { handle401(); throw new Error('Unauthenticated') }
  if (!res.ok) { const t = await res.text().catch(() => res.statusText); throw new Error(`${res.status}: ${t}`) }
  return res.json()
}
