import { AuthProvider, useAuth } from './auth/AuthContext'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'

function AppRoutes() {
  const { config, user, loading } = useAuth()
  if (loading) return <div className="flex min-h-screen items-center justify-center bg-gray-950"><p className="text-gray-500 text-sm">Loading…</p></div>
  if (config?.auth_enabled && !user) return <Login />
  return <Dashboard />
}

export default function App() {
  return <AuthProvider><AppRoutes /></AuthProvider>
}
