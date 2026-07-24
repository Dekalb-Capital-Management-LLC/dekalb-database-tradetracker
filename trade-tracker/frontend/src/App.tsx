import { AuthProvider, useAuth } from './auth/AuthContext'
import { AnalystProvider, useAnalyst } from './auth/AnalystContext'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
import ProfilePicker from './pages/ProfilePicker'
import ErrorBoundary from './components/ErrorBoundary'

function AppRoutes() {
  const { config, user, loading: authLoading } = useAuth()
  const { analyst, loading: analystLoading } = useAnalyst()

  if (authLoading || analystLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950">
        <p className="text-gray-500 text-sm">Loading…</p>
      </div>
    )
  }
  if (config?.auth_enabled && !user) return <Login />
  if (!analyst) return <ProfilePicker />
  if (!analyst.onboarded) return <Onboarding />
  return <Dashboard />
}

export default function App() {
  return (
    <ErrorBoundary label="Dashboard">
      <AuthProvider>
        <AnalystProvider>
          <AppRoutes />
        </AnalystProvider>
      </AuthProvider>
    </ErrorBoundary>
  )
}
