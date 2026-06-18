import { useEffect, useRef } from 'react'
import { useAuth } from '../auth/AuthContext'

declare global {
  interface Window { google?: { accounts: { id: { initialize: (c: object) => void; renderButton: (el: HTMLElement, c: object) => void } } } }
}

export default function Login() {
  const { config, setUser } = useAuth()
  const btnRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!config?.auth_enabled || !config.google_client_id) return
    const scriptId = 'google-gsi-client'
    const init = () => {
      if (!window.google || !btnRef.current) return
      window.google.accounts.id.initialize({ client_id: config.google_client_id, hd: config.allowed_domain, callback: handleCredential })
      window.google.accounts.id.renderButton(btnRef.current, { theme: 'filled_blue', size: 'large', width: 280 })
    }
    if (!document.getElementById(scriptId)) {
      const s = document.createElement('script'); s.id = scriptId
      s.src = 'https://accounts.google.com/gsi/client'; s.async = true; s.onload = init
      document.head.appendChild(s)
    } else { init() }
  }, [config])

  const handleCredential = async (response: { credential: string }) => {
    const res = await fetch('/api/auth/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id_token: response.credential }) })
    if (!res.ok) { const e = await res.json().catch(() => ({ detail: 'Unknown error' })); alert(`Sign-in failed: ${e.detail}`); return }
    const user = await res.json()
    localStorage.setItem('dekalb_id_token', response.credential)
    setUser(user)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <div className="w-full max-w-sm rounded-xl border border-gray-800 bg-gray-900 p-8 text-center shadow-xl">
        <p className="mb-1 text-xs font-bold tracking-widest text-blue-400 uppercase">DeKalb Capital Management</p>
        <h1 className="mb-2 text-xl font-semibold text-white">Trade Tracker</h1>
        <p className="mb-8 text-sm text-gray-400">Sign in with your <span className="text-gray-300">@{config?.allowed_domain}</span> account</p>
        <div className="flex justify-center"><div ref={btnRef} /></div>
      </div>
    </div>
  )
}
