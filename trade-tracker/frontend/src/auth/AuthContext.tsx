import { createContext, useContext, useEffect, useState, ReactNode } from 'react'

interface AuthConfig { auth_enabled: boolean; google_client_id: string; allowed_domain: string }
interface User { email: string; name: string; picture: string; sub: string }
interface AuthContextValue {
  config: AuthConfig | null; user: User | null; loading: boolean
  setUser: (u: User | null) => void; signOut: () => void; getIdToken: () => string | null
}

const TOKEN_KEY = 'dekalb_id_token'
const USER_KEY = 'dekalb_user'
const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null)
  const [user, setUserState] = useState<User | null>(() => {
    try { const s = localStorage.getItem(USER_KEY); return s ? JSON.parse(s) : null } catch { return null }
  })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/auth/config').then(r => r.json())
      .then((cfg: AuthConfig) => { setAuthConfig(cfg); if (!cfg.auth_enabled) setUserState(null) })
      .catch(() => setAuthConfig({ auth_enabled: false, google_client_id: '', allowed_domain: '' }))
      .finally(() => setLoading(false))
  }, [])

  const setUser = (u: User | null) => {
    setUserState(u)
    if (u) { localStorage.setItem(USER_KEY, JSON.stringify(u)) }
    else { localStorage.removeItem(USER_KEY); localStorage.removeItem(TOKEN_KEY) }
  }

  return (
    <AuthContext.Provider value={{ config: authConfig, user, loading, setUser,
      signOut: () => setUser(null), getIdToken: () => localStorage.getItem(TOKEN_KEY) }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
