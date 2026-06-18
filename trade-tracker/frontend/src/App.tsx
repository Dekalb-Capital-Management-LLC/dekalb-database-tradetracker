import { Routes, Route } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Trades from './pages/Trades'
import Import from './pages/Import'
import Login from './pages/Login'

function AppRoutes() {
  const { config, user, loading } = useAuth()
  if (loading) return <div className="flex min-h-screen items-center justify-center bg-gray-950"><p className="text-gray-500 text-sm">Loading…</p></div>
  if (config?.auth_enabled && !user) return <Login />
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="trades" element={<Trades />} />
        <Route path="import" element={<Import />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return <AuthProvider><AppRoutes /></AuthProvider>
}
