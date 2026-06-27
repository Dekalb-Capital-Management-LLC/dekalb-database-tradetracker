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

const STATUS_FALLBACK: Record<number, string> = {
  502: 'Bad gateway — the backend may be restarting',
  503: 'Service unavailable — the backend may be restarting',
  504: 'Request timed out — the backend is taking too long to respond',
}

// Surfaces a clean message for the UI: FastAPI's {detail: "..."} JSON body when
// present, otherwise a generic per-status message — never the raw response body,
// which for infra-level failures (e.g. an nginx 502/504 page) is a full HTML document.
async function extractErrorMessage(res: Response): Promise<string> {
  const contentType = res.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') return body.detail
    } catch {
      // fall through to generic message
    }
  }
  return STATUS_FALLBACK[res.status] ?? res.statusText ?? `Request failed (${res.status})`
}

async function throwIfNotOk(res: Response): Promise<void> {
  if (res.status === 401) { handle401(); throw new Error('Unauthenticated') }
  if (!res.ok) throw new Error(`${res.status}: ${await extractErrorMessage(res)}`)
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  await throwIfNotOk(res)
  return res.json()
}

export async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE', headers: authHeaders() })
  await throwIfNotOk(res)
  return res.json()
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) })
  await throwIfNotOk(res)
  return res.json()
}

export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: authHeaders(), body: form })
  await throwIfNotOk(res)
  return res.json()
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: body !== undefined ? JSON.stringify(body) : undefined })
  await throwIfNotOk(res)
  return res.json()
}
